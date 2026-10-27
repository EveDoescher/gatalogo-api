from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_database_session
from app.persistence_models import CatAvatar, CatPhoto, CatVisualEmbedding, RemoteCat, SyncChange, User
from app.dependencies import current_user
from app.schemas import SyncCatRecord, SyncOperation, SyncPullResponse, SyncPushRequest
from app.services.avatar_generation_service import rebuild_analysis_from_photos
from app.services.storage_service import photo_storage
from app.services.photo_cleanup import queue_photo_cleanup, drain_photo_cleanup
from app.services.vision_jobs import enqueue_recognition, enqueue_reference_analysis
from app.services.recognition_service import sync_catalog_sighting, refresh_cat_matches
from app.services.realtime_events import publish_committed_events
from app.services.progression import account as progress_account, identify

from app.services.catalog_sync_queries import sync_records
from app.services.catalog_changes import record_change as _record_change

router = APIRouter(prefix="/sync", tags=["sync"])


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _incoming_wins(existing: RemoteCat, operation: SyncOperation) -> bool:
    incoming = _utc(operation.deleted_at or operation.updated_at)
    current = _utc(existing.deleted_at or existing.client_updated_at)
    if existing.deleted_at is not None and operation.deleted_at is None:
        return False
    if incoming != current:
        return incoming > current
    return operation.device_id > existing.updated_by_device


async def _apply_operation(session: AsyncSession, user: User, operation: SyncOperation) -> RemoteCat:
    await progress_account(session, user.id)
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == operation.client_id).with_for_update())).scalar_one_or_none()
    if cat is not None and not _incoming_wins(cat, operation):
        return cat
    if cat is None:
        cat = RemoteCat(
            user_id=user.id,
            client_id=operation.client_id,
            payload={**operation.payload, "individual_id": None, "identity_revision": 0},
            analysis=operation.analysis,
            client_updated_at=_utc(operation.updated_at),
            updated_by_device=operation.device_id,
            deleted_at=_utc(operation.deleted_at) if operation.deleted_at else None,
        )
        session.add(cat)
    else:
        previous_identity = cat.payload.get("individual_id")
        revision = cat.payload.get("identity_revision", 0)
        cat.payload = {**operation.payload, "individual_id": previous_identity, "identity_revision": revision}
        if cat.analysis is None:
            cat.analysis = operation.analysis
        cat.client_updated_at = _utc(operation.updated_at)
        cat.updated_by_device = operation.device_id
        cat.deleted_at = _utc(operation.deleted_at) if operation.deleted_at else None
    await session.flush()
    if operation.deleted_at is None:
        if operation.payload.get("individual_id") == "pending":
            if not cat.payload.get("individual_id"):
                cat.payload = {**cat.payload, "identity_state": "pending"}
        else:
            await identify(session, cat, operation.payload.get("individual_id") or cat.client_id)
    await session.flush()
    await _record_change(session, cat)
    await sync_catalog_sighting(session, cat=cat, settings=get_settings())
    return cat


async def _last_revision(session: AsyncSession, cat_id: UUID) -> int:
    rows = await session.execute(select(SyncChange.revision).where(SyncChange.cat_id == cat_id).order_by(SyncChange.revision.desc()).limit(1))
    return rows.scalar_one_or_none() or 0


async def _to_record(session: AsyncSession, cat: RemoteCat) -> SyncCatRecord:
    return (await sync_records(session, [cat]))[0]


def _ordered_operations(operations):
    """A queued known-cat photo may arrive before its offline-created anchor."""
    pending = list(operations)
    ordered = []
    while pending:
        ids = {str(op.client_id) for op in pending}
        ready = [op for op in pending if op.deleted_at is not None or
                 not op.payload.get("individual_id") or
                 str(op.payload["individual_id"]) == str(op.client_id) or
                 str(op.payload["individual_id"]) not in ids]
        if not ready:
            raise HTTPException(422, "As identificações formam um ciclo. Marque um dos registros como indivíduo separado.")
        ordered.extend(ready)
        pending = [op for op in pending if op not in ready]
    return ordered


@router.post("/push")
async def push(payload: SyncPushRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict[str, int]:
    applied = 0
    for operation in _ordered_operations(payload.operations):
        photos_to_delete: list[str] = []
        cat = await _apply_operation(session, user, operation)
        if cat.client_updated_at == _utc(operation.updated_at):
            applied += 1
            if operation.deleted_at is not None and cat.photo_key:
                photos_to_delete.append(cat.photo_key)
                cat.photo_key = None
                cat.photo_hash = None
                cat.photo_content_type = None
            if operation.deleted_at is not None:
                references = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id))).scalars().all()
                photos_to_delete.extend(photo.storage_key for photo in references)
                avatar = await session.get(CatAvatar, cat.id)
                if avatar is not None:
                    photos_to_delete.extend((avatar.texture_keys or {}).values())
                    if avatar.preview_key:
                        photos_to_delete.append(avatar.preview_key)
        queue_photo_cleanup(cat, photos_to_delete)
    await session.commit()
    await publish_committed_events(session)
    await clean_expired_tombstones(session)
    await session.commit()
    await drain_photo_cleanup(session)
    return {"applied": applied}


@router.get("/pull", response_model=SyncPullResponse)
async def pull(
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> SyncPullResponse:
    changes = (await session.execute(select(SyncChange).where(SyncChange.user_id == user.id, SyncChange.revision > cursor).order_by(SyncChange.revision).limit(limit))).scalars().all()
    latest: dict[UUID, SyncChange] = {}
    for change in changes:
        latest[change.cat_id] = change
    cats_by_id = {
        cat.id: cat for cat in await session.scalars(
            select(RemoteCat).where(RemoteCat.id.in_(latest))
        )
    } if latest else {}
    cats = [cats_by_id[change.cat_id] for change in latest.values()
            if change.cat_id in cats_by_id]
    records = await sync_records(session, cats)
    next_cursor = changes[-1].revision if changes else cursor
    return SyncPullResponse(changes=records, next_cursor=next_cursor)


@router.put("/cats/{client_id}/photo", status_code=status.HTTP_204_NO_CONTENT)
async def upload_photo(
    client_id: UUID,
    updated_at: datetime = Form(...),
    device_id: str = Form(..., min_length=1, max_length=128),
    image: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> None:
    settings = get_settings()
    data = await image.read(settings.max_image_bytes + 1)
    await image.close()
    if len(data) > settings.max_image_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Imagem excede o tamanho permitido.")
    await progress_account(session, user.id)
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id).with_for_update())).scalar_one_or_none()
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gato não encontrado.")
    operation = SyncOperation(client_id=client_id, updated_at=updated_at, device_id=device_id)
    if not _incoming_wins(cat, operation) and cat.photo_key:
        return
    suffix = Path(image.filename or "photo.jpg").suffix.lower() or ".jpg"
    old_key = cat.photo_key
    queue_photo_cleanup(cat, [old_key])
    key, photo_hash = photo_storage.save(user_id=user.id, cat_id=cat.id, data=data, suffix=suffix)
    cat.photo_key = key
    cat.photo_hash = photo_hash
    cat.photo_content_type = image.content_type or "image/jpeg"
    primary = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == "primary").with_for_update())).scalar_one_or_none()
    content_changed = primary is None or primary.content_hash != photo_hash or primary.deleted_at is not None
    if primary is None:
        primary = CatPhoto(cat_id=cat.id, kind="primary", storage_key=key, content_hash=photo_hash, content_type=cat.photo_content_type, status="ready")
        session.add(primary)
    else:
        primary.storage_key = key
        primary.content_hash = photo_hash
        primary.content_type = cat.photo_content_type
        primary.deleted_at = None
        if content_changed:
            primary.analysis = None
    cat.client_updated_at = _utc(updated_at)
    cat.updated_by_device = device_id
    await _record_change(session, cat)
    await session.flush()
    if content_changed:
        await session.execute(delete(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == primary.id))
    if cat.analysis is not None or (cat.payload or {}).get("status") != "pending_analysis":
        await enqueue_recognition(session, cat=cat, photo=primary)
    if content_changed:
        await refresh_cat_matches(session, cat=cat, settings=settings)
    await session.commit()
    await publish_committed_events(session)
    await drain_photo_cleanup(session)


@router.get("/cats/{client_id}/photo")
async def download_photo(client_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FileResponse:
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)))).scalar_one_or_none()
    if cat is None or not cat.photo_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto não encontrada.")
    try:
        path = photo_storage.open(cat.photo_key)
    except FileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto não encontrada.") from error
    return FileResponse(path, media_type=cat.photo_content_type or "image/jpeg", filename=f"{client_id}{path.suffix}")


@router.put("/cats/{client_id}/reference-photos/{kind}", status_code=status.HTTP_202_ACCEPTED)
async def upload_reference_photo(
    client_id: UUID,
    kind: str,
    image: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict[str, str]:
    if kind not in {"front", "left", "right", "back"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ângulo de referência inválido.")
    data = await image.read(get_settings().max_image_bytes + 1)
    await image.close()
    if len(data) > get_settings().max_image_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Imagem excede o tamanho permitido.")
    await progress_account(session, user.id)
    cat = (await session.execute(
        select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)).with_for_update()
    )).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gato não encontrado.")
    suffix = Path(image.filename or "reference.jpg").suffix.lower() or ".jpg"
    photo = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == kind).with_for_update())).scalar_one_or_none()
    queue_photo_cleanup(cat, [photo.storage_key if photo else None])
    key, content_hash = photo_storage.save_reference(user_id=user.id, cat_id=cat.id, kind=kind, data=data, suffix=suffix)
    if photo is None:
        photo = CatPhoto(cat_id=cat.id, kind=kind, storage_key=key, content_hash=content_hash, content_type=image.content_type or "image/jpeg", status="analyzing")
        session.add(photo)
    else:
        photo.storage_key = key
        photo.content_hash = content_hash
        photo.content_type = image.content_type or "image/jpeg"
        photo.status = "analyzing"
        photo.deleted_at = None
        photo.analysis = None
    await _record_change(session, cat)
    await session.flush()
    await session.execute(delete(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == photo.id))
    await enqueue_recognition(session, cat=cat, photo=photo)
    await enqueue_reference_analysis(session, cat=cat, photo=photo)
    await refresh_cat_matches(session, cat=cat, settings=get_settings())
    await session.commit()
    await publish_committed_events(session)
    await drain_photo_cleanup(session)
    return {"status": "analyzing"}


@router.get("/cats/{client_id}/reference-photos/{kind}")
async def download_reference_photo(client_id: UUID, kind: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FileResponse:
    photo = (await session.execute(
        select(CatPhoto).join(RemoteCat, CatPhoto.cat_id == RemoteCat.id).where(
            RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None), CatPhoto.kind == kind, CatPhoto.deleted_at.is_(None)
        )
    )).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto de referência não encontrada.")
    return FileResponse(photo_storage.open(photo.storage_key), media_type=photo.content_type)


@router.delete("/cats/{client_id}/reference-photos/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reference_photo(client_id: UUID, kind: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    await progress_account(session, user.id)
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)).with_for_update())).scalar_one_or_none()
    photo = (await session.execute(select(CatPhoto).join(RemoteCat, CatPhoto.cat_id == RemoteCat.id).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, CatPhoto.kind == kind, CatPhoto.deleted_at.is_(None)).with_for_update())).scalar_one_or_none()
    if cat is None or photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto de referência não encontrada.")
    queue_photo_cleanup(cat, [photo.storage_key])
    photo.deleted_at = datetime.now(UTC)
    await session.execute(delete(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == photo.id))
    await refresh_cat_matches(session, cat=cat, settings=get_settings())
    await rebuild_analysis_from_photos(session, cat)
    await _record_change(session, cat)
    await session.commit()
    await publish_committed_events(session)
    await drain_photo_cleanup(session)


@router.get("/cats/{client_id}/avatar/{asset_name}")
async def download_avatar_asset(client_id: UUID, asset_name: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FileResponse:
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)))).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gato não encontrado.")
    avatar = await session.get(CatAvatar, cat.id)
    key = avatar.preview_key if avatar and asset_name == "preview" else (avatar.texture_keys or {}).get(asset_name) if avatar else None
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artefato do avatar não encontrado.")
    path = photo_storage.open(key)
    return FileResponse(path, media_type="image/webp" if path.suffix == ".webp" else "image/png")


async def clean_expired_tombstones(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().tombstone_retention_days)
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.deleted_at.is_not(None), RemoteCat.deleted_at < cutoff).with_for_update())).scalars().all()
    for cat in cats:
        keys = [cat.photo_key]
        references = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id))).scalars().all()
        for reference in references:
            keys.append(reference.storage_key)
        avatar = await session.get(CatAvatar, cat.id)
        if avatar is not None:
            keys.extend((avatar.texture_keys or {}).values())
            keys.append(avatar.preview_key)
        queue_photo_cleanup(cat, keys)
        await session.delete(cat)
    return len(cats)
