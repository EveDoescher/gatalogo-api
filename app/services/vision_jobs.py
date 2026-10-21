from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import CatPhoto, RemoteCat, VisionJob


REFERENCE_ANALYSIS_PREFIX = "analysis:"


async def enqueue_recognition(
    session: AsyncSession,
    *,
    cat: RemoteCat,
    photo: CatPhoto,
) -> VisionJob:
    return await _enqueue(session, cat=cat, photo=photo, kind=photo.kind)


async def enqueue_reference_analysis(session, *, cat, photo):
    return await _enqueue(
        session, cat=cat, photo=photo, kind=REFERENCE_ANALYSIS_PREFIX + photo.kind
    )


async def _enqueue(session, *, cat, photo, kind):
    existing = (
        await session.execute(
            select(VisionJob).where(
                VisionJob.cat_id == cat.id,
                VisionJob.kind == kind,
                VisionJob.photo_id == photo.id,
                VisionJob.photo_hash == photo.content_hash,
                VisionJob.status.in_(("pending", "running")),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    job = VisionJob(
        user_id=cat.user_id,
        cat_id=cat.id,
        photo_id=photo.id,
        kind=kind,
        photo_hash=photo.content_hash,
    )
    session.add(job)
    await session.flush()
    return job
