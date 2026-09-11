from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import CatAvatar, RemoteCat, SyncChange
from app.services.avatar_service import avatar_service


def merge_coat_evidence(current: dict | None, incoming: dict | None) -> dict | None:
    """Merge only observed regions; unknown sides never overwrite existing data."""
    if not incoming:
        return current
    if not current:
        return deepcopy(incoming)
    merged = deepcopy(current)
    current_map = dict(merged.get("pattern_map") or {})
    incoming_map = dict(incoming.get("pattern_map") or {})
    regions = {item.get("region"): item for item in current_map.get("regions", [])}
    for candidate in incoming_map.get("regions", []):
        if candidate.get("visibility") == "UNKNOWN" or not candidate.get("region"):
            continue
        old = regions.get(candidate["region"])
        if old is None or (candidate.get("confidence") or 0) >= (old.get("confidence") or 0):
            regions[candidate["region"]] = candidate
    current_map["regions"] = list(regions.values())
    current_map["accent_colors"] = list(
        dict.fromkeys([
            *(current_map.get("accent_colors") or []),
            *(incoming_map.get("accent_colors") or []),
        ])
    )[:3]
    if not current_map.get("base_color"):
        current_map["base_color"] = incoming_map.get("base_color")
    merged["pattern_map"] = current_map
    return merged


async def generate_avatar(session: AsyncSession, cat: RemoteCat) -> CatAvatar:
    avatar = await session.get(CatAvatar, cat.id, with_for_update=True)
    version = (avatar.version + 1) if avatar else 1
    if avatar is None:
        avatar = CatAvatar(cat_id=cat.id, version=version, status="generating", texture_keys={})
        session.add(avatar)
    else:
        avatar.version = version
        avatar.status = "generating"
        avatar.error_message = None
    await session.flush()
    try:
        texture_keys, preview_key, coat_map, asset_hash = await asyncio.to_thread(
            avatar_service.save,
            user_id=cat.user_id,
            cat_id=cat.id,
            analysis=cat.analysis,
            version=version,
        )
        avatar.status = "ready"
        avatar.texture_keys = texture_keys
        avatar.preview_key = preview_key
        avatar.coat_map = coat_map
        avatar.asset_hash = asset_hash
        avatar.updated_at = datetime.now(UTC)
    except Exception as error:  # The catalogue remains available without an avatar.
        avatar.status = "failed"
        avatar.error_message = str(error)[:240]
        avatar.updated_at = datetime.now(UTC)
    session.add(SyncChange(user_id=cat.user_id, cat_id=cat.id))
    return avatar


async def rebuild_analysis_from_photos(session: AsyncSession, cat: RemoteCat) -> None:
    """Restore the primary analysis and layer only live reference evidence."""
    from app.persistence_models import CatPhoto

    photos = (await session.execute(
        select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.deleted_at.is_(None))
    )).scalars().all()
    primary = next((photo.analysis for photo in photos if photo.kind == "primary" and photo.analysis), cat.analysis)
    merged = primary
    for photo in photos:
        if photo.kind != "primary":
            merged = merge_coat_evidence(merged, photo.analysis)
    cat.analysis = merged


async def get_avatar(session: AsyncSession, cat_id) -> CatAvatar | None:
    return (await session.execute(select(CatAvatar).where(CatAvatar.cat_id == cat_id))).scalar_one_or_none()
