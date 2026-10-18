"""Durable production pushes; inbox state and collection never depend on FCM."""
import logging
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy import select
from app.persistence_models import AppNotification, HabitatTransaction
from app.services.fcm_service import fcm_sender
from app.quintal_models import QuintalTask, QuintalPlot, QuintalFacility, QuintalResident

logger = logging.getLogger(__name__)


async def still_available(session, user_id, markers):
    if markers is None:
        return True  # Compatibility with receipts saved before readiness markers.
    for marker in markers:
        kind, rest = marker.split(':', 1)
        if kind == 'task':
            task = await session.get(QuintalTask, UUID(rest))
            if task and task.user_id == user_id and task.status == 'ready':
                return True
        elif kind == 'plot':
            slot, started = rest.split(':', 1)
            plot = await session.get(QuintalPlot, (user_id, int(slot)))
            if plot and plot.crop and plot.started_at:
                current = plot.started_at.replace(tzinfo=UTC) if plot.started_at.tzinfo is None else plot.started_at
                if current == datetime.fromisoformat(started):
                    return True
        elif kind == 'build':
            facility, level, _ = rest.split(':', 2)
            row = await session.get(QuintalFacility, (user_id, facility))
            if row and row.target_level == int(level):
                return True
        elif kind == 'resident':
            resident, cycle = rest.split(':', 1)
            row = await session.get(QuintalResident, (user_id, resident))
            if (row and row.state.get('ready', 0) > 0
                    and str(row.state.get('ready_since')) == cycle):
                return True
    return False


async def deliver(factory, receipt_id, *, now=None, sender=None):
    now = now or datetime.now(UTC)
    sender = sender or fcm_sender
    async with factory() as session:
        receipt = await session.scalar(select(HabitatTransaction).where(
            HabitatTransaction.id == receipt_id,
            HabitatTransaction.category == 'quintal_push',
        ).with_for_update(skip_locked=True))
        if receipt is None:
            return
        state = dict(receipt.details)
        if state['state'] != 'pending' or state.get('next_attempt', 0) > now.timestamp():
            return
        notice = await session.get(AppNotification, UUID(state['notification_id']))
        if (notice is None or notice.read_at or now.timestamp() >= state['expires_at']
                or not await still_available(session, receipt.user_id, state.get('markers'))):
            state['state'] = 'dismissed'
        else:
            try:
                report = await sender.send_notification(user_id=receipt.user_id,
                    notification_id=state['notification_id'], notification_type='quintal_ready',
                    delivered=state.get('delivered', []))
                state['delivered'] = report['delivered']
                retry = report['retry']
            except Exception:
                logger.exception('Production push failed; keeping durable retry')
                retry = True
            state['attempts'] += 1
            state['state'] = 'pending' if retry else 'sent'
            state['next_attempt'] = now.timestamp() + min(3600, 60 * 2 ** min(state['attempts'] - 1, 6))
        receipt.details = state
        await session.commit()


async def deliver_pending(factory, *, now=None, sender=None):
    now = now or datetime.now(UTC)
    async with factory() as session:
        ids = (await session.scalars(select(HabitatTransaction.id).where(
            HabitatTransaction.category == 'quintal_push',
            HabitatTransaction.details['state'].as_string() == 'pending',
            HabitatTransaction.details['next_attempt'].as_float() <= now.timestamp(),
        ).order_by(HabitatTransaction.created_at).limit(100))).all()
    for receipt_id in ids:
        try:
            await deliver(factory, receipt_id, now=now, sender=sender)
        except Exception:
            logger.exception('Could not process one production push; continuing the queue')
