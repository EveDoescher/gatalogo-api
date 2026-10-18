"""Advance durable production and readiness notices without an app request."""
import asyncio
import logging
from contextlib import suppress

from sqlalchemy import select

from app.persistence_models import User
from app.quintal_models import QuintalFacility
from app.services import progression
from app.services.quintal_production import projection
from app.services.quintal_progress import publish_progress
from app.services.quintal_notifications import notify_ready
from app.services.realtime_events import publish_committed_events
from app.services.quintal_push import deliver_pending

logger = logging.getLogger(__name__)


async def process_account(factory, user_id, *, now=None):
    async with factory() as session:
        # Same lock order as the HTTP routes. Other API workers skip busy users.
        user = await session.scalar(select(User.id).where(
            User.id == user_id, User.status == 'active'
        ).with_for_update(skip_locked=True))
        if user is None:
            return
        await progression.account(session, user_id)
        state = await projection(session, user_id, now=now)
        await publish_progress(session, user_id)
        await notify_ready(session, user_id, state)
        await session.commit()
        await publish_committed_events(session)


async def sweep(factory, *, now=None):
    cursor = None
    while True:
        async with factory() as session:
            query = select(QuintalFacility.user_id).distinct().order_by(
                QuintalFacility.user_id
            ).limit(100)
            if cursor is not None:
                query = query.where(QuintalFacility.user_id > cursor)
            users = list((await session.scalars(query)).all())
        if not users:
            return
        for user_id in users:
            try:
                await process_account(factory, user_id, now=now)
            except Exception:
                logger.exception('Could not process Quintal readiness')
        cursor = users[-1]


class QuintalScheduler:
    def __init__(self, factory, interval=60):
        self.factory = factory
        self.interval = interval
        self.task = None

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name='quintal-readiness')

    async def _run(self):
        while True:
            for operation in (sweep, deliver_pending):
                try:
                    await operation(self.factory)
                except Exception:
                    logger.exception('Quintal background operation failed; retrying next interval')
            await asyncio.sleep(self.interval)

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
