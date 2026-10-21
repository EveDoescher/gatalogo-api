"""Durable reference jobs with short claims and fenced results.

Inference runs without an open database connection. Expired claims are retried,
and their late results cannot overwrite the newer attempt.
"""

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select

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


@dataclass(frozen=True)
class AnalysisClaim:
    job_id: UUID
    user_id: UUID
    cat_id: UUID
    photo_id: UUID
    photo_hash: str
    client_id: str
    storage_key: str
    attempt: int


async def _claim(factory, settings):
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=settings.vision_job_lease_seconds)
    async with factory() as session:
        job = await session.scalar(
            select(VisionJob)
            .where(
                VisionJob.kind.startswith(REFERENCE_ANALYSIS_PREFIX),
                or_(
                    VisionJob.status == "pending",
                    and_(VisionJob.status == "running", VisionJob.started_at < expired),
                ),
            )
            .order_by(VisionJob.created_at, VisionJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        cat = await session.get(RemoteCat, job.cat_id)
        photo = await session.get(CatPhoto, job.photo_id) if job.photo_id else None
        if not _current(cat, photo, job.photo_hash):
            job.status, job.completed_at = "superseded", now
            await session.commit()
            return False
        # The monotonically increasing attempt fences results from expired consumers.
        job.status, job.started_at = "running", now
        job.attempts += 1
        claim = AnalysisClaim(
            job.id,
            job.user_id,
            job.cat_id,
            job.photo_id,
            job.photo_hash,
            str(cat.client_id),
            photo.storage_key,
            job.attempts,
        )
        await session.commit()
        return claim


def _read_image(claim, settings):
    with photo_storage.open(claim.storage_key).open("rb") as source:
        data = source.read(settings.max_image_bytes + 1)
    if (
        len(data) > settings.max_image_bytes
        or hashlib.sha256(data).hexdigest() != claim.photo_hash
    ):
        raise ValueError("O conteúdo da foto mudou durante o processamento.")
    return data


async def _finish(
    factory, claim, settings, *, incoming=None, error=None, cancelled=False
):
    async with factory() as session:
        # Match upload/progression order; never wait on a user while holding a job lock.
        await account(session, claim.user_id)
        cat = await session.get(RemoteCat, claim.cat_id, with_for_update=True)
        photo = await session.get(CatPhoto, claim.photo_id, with_for_update=True)
        job = await session.get(VisionJob, claim.job_id, with_for_update=True)
        if job is None or job.status != "running" or job.attempts != claim.attempt:
            return
        if not _current(cat, photo, claim.photo_hash):
            job.status, job.completed_at = "superseded", datetime.now(UTC)
        elif cancelled:
            job.status, job.started_at = "pending", None
        else:
            if error is None:
                try:
                    async with session.begin_nested():
                        photo.analysis, photo.status = incoming, "ready"
                        await confirm_photo(
                            session, cat, photo, incoming, reference=True
                        )
                        await rebuild_analysis_from_photos(session, cat)
                        await record_change(session, cat)
                except Exception as failure:
                    logger.exception("Reference result failed for job %s", claim.job_id)
                    discard_pending_events(session)
                    error = failure
            if error is None:
                job.status, job.error_message = "completed", None
                job.completed_at = datetime.now(UTC)
            else:
                terminal = claim.attempt >= settings.vision_job_max_attempts
                job.status = "failed" if terminal else "pending"
                job.error_message = str(error)[:2000]
                job.completed_at = datetime.now(UTC) if terminal else None
                if terminal:
                    # A rolled-back savepoint expires photo attributes.
                    await session.refresh(photo)
                    await session.refresh(cat)
                    photo.status = "failed"
                    await record_change(session, cat)
        await session.commit()
        await publish_committed_events(session)


async def process_next(factory, *, settings=None, analyzer=None):
    settings = settings or get_settings()
    analyzer = analyzer or analyze_reference
    claim = await _claim(factory, settings)
    if claim is None:
        return False
    if claim is False:
        return True
    try:
        if claim.attempt > settings.vision_job_max_attempts:
            raise TimeoutError("A tentativa anterior excedeu o prazo de processamento.")
        # No session, transaction or database connection is retained during inference.
        data = await asyncio.to_thread(_read_image, claim, settings)
        async with asyncio.timeout(settings.vision_job_lease_seconds):
            incoming = await analyzer(cat_id=claim.client_id, image_bytes=data)
    except asyncio.CancelledError:
        await _finish(factory, claim, settings, cancelled=True)
        raise
    except Exception as error:
        logger.exception("Reference analysis failed for job %s", claim.job_id)
        await _finish(factory, claim, settings, error=error)
    else:
        await _finish(factory, claim, settings, incoming=incoming)
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
