"""Durable reference analysis, independent from the GPU recognition worker.

A pending job is committed with its upload. A consumer holds only the job row
lock across inference: another API process skips it, and process death rolls the
claim back to pending. Cat and account locks are acquired after inference, in the
established progression lock order. Cancellation rolls back for restart recovery.
"""

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.persistence_models import CatPhoto, RemoteCat, VisionJob
from app.services.avatar_generation_service import rebuild_analysis_from_photos
from app.services.catalog_changes import record_change
from app.services.progression import account, confirm_photo
from app.services.realtime_events import (
    discard_pending_events,
    publish_committed_events,
)
from app.services.storage_service import photo_storage
from app.services.vision_jobs import REFERENCE_ANALYSIS_PREFIX

logger = logging.getLogger(__name__)


async def analyze_reference(*, cat_id, image_bytes):
    from app.services.analysis_service import AnalysisService

    return (
        await AnalysisService(get_settings()).analyze(
            cat_id=cat_id, image_bytes=image_bytes
        )
    ).model_dump(mode="json")


def _current(cat, photo, expected_hash):
    return (
        cat is not None
        and cat.deleted_at is None
        and photo is not None
        and photo.deleted_at is None
        and photo.content_hash == expected_hash
    )


async def process_next(factory, *, settings=None, analyzer=None):
    settings = settings or get_settings()
    analyzer = analyzer or analyze_reference
    async with factory() as session:
        job = await session.scalar(
            select(VisionJob)
            .where(
                VisionJob.status == "pending",
                VisionJob.kind.startswith(REFERENCE_ANALYSIS_PREFIX),
            )
            .order_by(VisionJob.created_at, VisionJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return False
        job.status, job.started_at = "running", datetime.now(UTC)
        job.attempts += 1
        cat = await session.get(RemoteCat, job.cat_id)
        photo = await session.get(CatPhoto, job.photo_id) if job.photo_id else None
        if not _current(cat, photo, job.photo_hash):
            job.status, job.completed_at = "superseded", datetime.now(UTC)
            await session.commit()
            return True
        try:
            # Savepoint keeps the claim locked even when analysis/progression fails.
            async with session.begin_nested():
                with photo_storage.open(photo.storage_key).open("rb") as source:
                    data = source.read(settings.max_image_bytes + 1)
                if (
                    len(data) > settings.max_image_bytes
                    or hashlib.sha256(data).hexdigest() != job.photo_hash
                ):
                    raise ValueError(
                        "O conteúdo da foto mudou durante o processamento."
                    )
                async with asyncio.timeout(settings.vision_job_lease_seconds):
                    incoming = await analyzer(
                        cat_id=str(cat.client_id), image_bytes=data
                    )
                await account(session, job.user_id)
                await session.refresh(cat, with_for_update=True)
                await session.refresh(photo, with_for_update=True)
                if not _current(cat, photo, job.photo_hash):
                    job.status = "superseded"
                else:
                    photo.analysis, photo.status = incoming, "ready"
                    await confirm_photo(session, cat, photo, incoming, reference=True)
                    await rebuild_analysis_from_photos(session, cat)
                    await record_change(session, cat)
                    job.status = "completed"
                job.error_message, job.completed_at = None, datetime.now(UTC)
        except Exception as error:
            logger.exception("Reference analysis failed for job %s", job.id)
            # Events from rolled-back progression must never escape this attempt.
            discard_pending_events(session)
            job.error_message = str(error)[:2000]
            terminal = job.attempts >= settings.vision_job_max_attempts
            job.status = "failed" if terminal else "pending"
            job.completed_at = datetime.now(UTC) if terminal else None
            if terminal:
                # Preserve the upload lock order even on failure.
                await account(session, job.user_id)
                await session.refresh(cat, with_for_update=True)
                await session.refresh(photo, with_for_update=True)
                if _current(cat, photo, job.photo_hash):
                    photo.status = "failed"
                    await record_change(session, cat)
                else:
                    job.status = "superseded"
        await session.commit()
        await publish_committed_events(session)
        return True


class ReferenceAnalysisWorker:
    def __init__(self, factory, *, settings=None):
        self.factory = factory
        self.settings = settings or get_settings()
        self.task = None

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name="reference-analysis")

    async def _run(self):
        while True:
            try:
                await process_next(self.factory, settings=self.settings)
            except Exception:
                logger.exception("Reference queue unavailable; retrying next interval")
            await asyncio.sleep(self.settings.vision_worker_poll_seconds)

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
