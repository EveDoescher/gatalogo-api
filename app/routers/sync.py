from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_database_session, get_session_factory
from app.persistence_models import CatAvatar, CatPhoto, CatVisualEmbedding, RemoteCat, SyncChange, User
from app.routers.auth import current_user
from app.schemas import ReferencePhotoResponse, SyncCatRecord, SyncOperation, SyncPullResponse, SyncPushRequest
from app.services.analysis_service import AnalysisService
from app.services.avatar_generation_service import rebuild_analysis_from_photos
from app.services.storage_service import photo_storage
from app.services.vision_jobs import enqueue_recognition
from app.services.recognition_service import sync_catalog_sighting, refresh_cat_matches
from app.services.realtime_events import publish_committed_events

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


async def _record_change(session: AsyncSession, cat: RemoteCat) -> None:
    session.add(SyncChange(user_id=cat.user_id, cat_id=cat.id))
    await session.flush()


async def _apply_operation(session: AsyncSession, user: User, operation: SyncOperation) -> RemoteCat:
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == operation.client_id).with_for_update())).scalar_one_or_none()
    if cat is not None and not _incoming_wins(cat, operation):
        return cat
    if cat is None:
        cat = RemoteCat(
            user_id=user.id,
            client_id=operation.client_id,
            payload=operation.payload,
            analysis=operation.analysis,
            client_updated_at=_utc(operation.updated_at),
            updated_by_device=operation.device_id,
            deleted_at=_utc(operation.deleted_at) if operation.deleted_at else None,
        )
        session.add(cat)
    else:
        cat.payload = operation.payload
        cat.analysis = operation.analysis
        cat.client_updated_at = _utc(operation.updated_at)
        cat.updated_by_device = operation.device_id
        cat.deleted_at = _utc(operation.deleted_at) if operation.deleted_at else None
    await session.flush()
    await _record_change(session, cat)
    await sync_catalog_sighting(session, cat=cat, settings=get_settings())
    return cat


async def _last_revision(session: AsyncSession, cat_id: UUID) -> int:
    rows = await session.execute(select(SyncChange.revision).where(SyncChange.cat_id == cat_id).order_by(SyncChange.revision.desc()).limit(1))
    return rows.scalar_one_or_none() or 0


async def _to_record(session: AsyncSession, cat: RemoteCat) -> SyncCatRecord:
    revision = await _last_revision(session, cat.id)
    photos = (await session.execute(
        select(CatPhoto).where(
            CatPhoto.cat_id == cat.id,
            CatPhoto.kind.in_(("front", "left", "right", "back")),
            CatPhoto.deleted_at.is_(None),
        )
    )).scalars().all()
    return SyncCatRecord(
        client_id=cat.client_id,
        updated_at=cat.client_updated_at,
        device_id=cat.updated_by_device,
        deleted_at=cat.deleted_at,
        payload=cat.payload or {},
        analysis=cat.analysis,
        photo_available=bool(cat.photo_key and cat.deleted_at is None),
        photo_url=f"/sync/cats/{cat.client_id}/photo" if cat.photo_key and cat.deleted_at is None else None,
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


@router.post("/push")
async def push(payload: SyncPushRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict[str, int]:
    applied = 0
    photos_to_delete: list[str] = []
    for operation in payload.operations:
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
    await session.commit()
    await publish_committed_events(session)
    for key in photos_to_delete:
        photo_storage.delete(key)
    await clean_expired_tombstones(session)
    await session.commit()
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
    records: list[SyncCatRecord] = []
    for change in latest.values():
        cat = await session.get(RemoteCat, change.cat_id)
        if cat is not None:
            records.append(await _to_record(session, cat))
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
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id).with_for_update())).scalar_one_or_none()
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gato não encontrado.")
    operation = SyncOperation(client_id=client_id, updated_at=updated_at, device_id=device_id)
    if not _incoming_wins(cat, operation) and cat.photo_key:
        return
    suffix = Path(image.filename or "photo.jpg").suffix.lower() or ".jpg"
    old_key = cat.photo_key
    key, photo_hash = photo_storage.save(user_id=user.id, cat_id=cat.id, data=data, suffix=suffix)
    cat.photo_key = key
    cat.photo_hash = photo_hash
    cat.photo_content_type = image.content_type or "image/jpeg"
    primary = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == "primary").with_for_update())).scalar_one_or_none()
    if primary is None:
        primary = CatPhoto(cat_id=cat.id, kind="primary", storage_key=key, content_hash=photo_hash, content_type=cat.photo_content_type, status="ready")
        session.add(primary)
    else:
        primary.storage_key = key
        primary.content_hash = photo_hash
        primary.content_type = cat.photo_content_type
        primary.deleted_at = None
        primary.analysis = None
    cat.client_updated_at = _utc(updated_at)
    cat.updated_by_device = device_id
    await _record_change(session, cat)
    await session.flush()
    await session.execute(delete(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == primary.id))
    await enqueue_recognition(session, cat=cat, photo=primary)
    await refresh_cat_matches(session, cat=cat, settings=settings)
    await session.commit()
    await publish_committed_events(session)
    if old_key and old_key != key:
        photo_storage.delete(old_key)


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


async def _analyze_reference_photo(
    *, user_id, client_id: UUID, kind: str, image_bytes: bytes
) -> None:
    """Best-effort background work; a failed reference can never block the cat."""
    async with get_session_factory()() as session:
        cat = (await session.execute(
            select(RemoteCat).where(RemoteCat.user_id == user_id, RemoteCat.client_id == client_id)
        )).scalar_one_or_none()
        photo = (await session.execute(
            select(CatPhoto).join(RemoteCat, CatPhoto.cat_id == RemoteCat.id).where(
                RemoteCat.user_id == user_id, RemoteCat.client_id == client_id, CatPhoto.kind == kind
            )
        )).scalar_one_or_none()
        if cat is None or photo is None or photo.deleted_at is not None:
            return
        try:
            incoming = (await AnalysisService(get_settings()).analyze(
                cat_id=str(client_id), image_bytes=image_bytes
            )).model_dump(mode="json")
            await session.refresh(cat, with_for_update=True)
            await session.refresh(photo, with_for_update=True)
            if cat.deleted_at is not None or photo.deleted_at is not None or photo.content_hash != hashlib.sha256(image_bytes).hexdigest():
                return
            photo.analysis = incoming
            photo.status = "ready"
            await rebuild_analysis_from_photos(session, cat)
            await _record_change(session, cat)
        except Exception:
            await session.rollback()
            await session.refresh(cat)
            await session.refresh(photo)
            if photo.deleted_at is not None or photo.content_hash != hashlib.sha256(image_bytes).hexdigest():
                return
            photo.status = "failed"
            await _record_change(session, cat)
        await session.commit()


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
    cat = (await session.execute(
        select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)).with_for_update()
    )).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gato não encontrado.")
    suffix = Path(image.filename or "reference.jpg").suffix.lower() or ".jpg"
    key, content_hash = photo_storage.save_reference(user_id=user.id, cat_id=cat.id, kind=kind, data=data, suffix=suffix)
    photo = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == kind).with_for_update())).scalar_one_or_none()
    old_key = photo.storage_key if photo else None
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
    await refresh_cat_matches(session, cat=cat, settings=get_settings())
    await session.commit()
    await publish_committed_events(session)
    if old_key and old_key != key:
        photo_storage.delete(old_key)
    asyncio.create_task(_analyze_reference_photo(user_id=user.id, client_id=client_id, kind=kind, image_bytes=data))
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
    cat = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, RemoteCat.deleted_at.is_(None)).with_for_update())).scalar_one_or_none()
    photo = (await session.execute(select(CatPhoto).join(RemoteCat, CatPhoto.cat_id == RemoteCat.id).where(RemoteCat.user_id == user.id, RemoteCat.client_id == client_id, CatPhoto.kind == kind, CatPhoto.deleted_at.is_(None)).with_for_update())).scalar_one_or_none()
    if cat is None or photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto de referência não encontrada.")
    photo.deleted_at = datetime.now(UTC)
    await session.execute(delete(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == photo.id))
    await refresh_cat_matches(session, cat=cat, settings=get_settings())
    await rebuild_analysis_from_photos(session, cat)
    await _record_change(session, cat)
    await session.commit()
    await publish_committed_events(session)
    photo_storage.delete(photo.storage_key)


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
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.deleted_at.is_not(None), RemoteCat.deleted_at < cutoff))).scalars().all()
    for cat in cats:
        photo_storage.delete(cat.photo_key)
        references = (await session.execute(select(CatPhoto).where(CatPhoto.cat_id == cat.id))).scalars().all()
        for reference in references:
            photo_storage.delete(reference.storage_key)
        avatar = await session.get(CatAvatar, cat.id)
        if avatar is not None:
            for key in (avatar.texture_keys or {}).values():
                photo_storage.delete(key)
            photo_storage.delete(avatar.preview_key)
        await session.delete(cat)
    return len(cats)
