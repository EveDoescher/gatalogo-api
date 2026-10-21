"""Batch catalog sync presentation without changing cursors or transactions."""

from collections import defaultdict
from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import CatPhoto, RemoteCat, SyncChange
from app.schemas import ReferencePhotoResponse, SyncCatRecord


def record_response(
    cat: RemoteCat, *, revision: int, photos: Iterable[CatPhoto]
) -> SyncCatRecord:
    return SyncCatRecord(
        client_id=cat.client_id,
        updated_at=cat.client_updated_at,
        device_id=cat.updated_by_device,
        deleted_at=cat.deleted_at,
        payload=cat.payload or {},
        analysis=cat.analysis,
        photo_available=bool(cat.photo_key and cat.deleted_at is None),
        photo_url=f"/sync/cats/{cat.client_id}/photo"
        if cat.photo_key and cat.deleted_at is None
        else None,
        reference_photos=[
            ReferencePhotoResponse(
                kind=photo.kind,
                status=photo.status,
                photo_url=f"/sync/cats/{cat.client_id}/reference-photos/{photo.kind}",
                updated_at=photo.updated_at,
            )
            for photo in photos
        ],
        avatar=None,
        revision=revision,
    )


async def sync_records(
    session: AsyncSession, cats: Sequence[RemoteCat]
) -> list[SyncCatRecord]:
    if not cats:
        return []
    ids = [cat.id for cat in cats]
    revisions = dict(
        (
            await session.execute(
                select(SyncChange.cat_id, func.max(SyncChange.revision))
                .where(SyncChange.cat_id.in_(ids))
                .group_by(SyncChange.cat_id)
            )
        ).all()
    )
    photos = defaultdict(list)
    rows = await session.scalars(
        select(CatPhoto).where(
            CatPhoto.cat_id.in_(ids),
            CatPhoto.kind.in_(("front", "left", "right", "back")),
            CatPhoto.deleted_at.is_(None),
        )
    )
    for photo in rows:
        photos[photo.cat_id].append(photo)
    return [
        record_response(cat, revision=revisions.get(cat.id, 0), photos=photos[cat.id])
        for cat in cats
    ]
