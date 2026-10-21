"""Shared report-photo resolution and thumbnail generation.

The public route additionally checks poster visibility before using this service.
"""

from io import BytesIO
from uuid import UUID

from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import CatPhoto, MissingReport, RemoteCat
from app.services.storage_service import photo_storage


async def report_photo_bytes(session: AsyncSession, report_id: UUID) -> bytes:
    report = await session.get(MissingReport, report_id)
    if report is None or report.status != "active":
        raise HTTPException(404, "Alerta não encontrado ou inativo.")
    cat = await session.get(RemoteCat, report.cat_id)
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(404, "Gato não encontrado.")
    photo = (
        await session.execute(
            select(CatPhoto).where(
                CatPhoto.cat_id == cat.id,
                CatPhoto.kind == "primary",
                CatPhoto.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    storage_key = photo.storage_key if photo else cat.photo_key
    if not storage_key:
        raise HTTPException(404, "Foto não disponível.")
    return photo_thumbnail_bytes(storage_key)


def photo_thumbnail_bytes(storage_key: str) -> bytes:
    """Strip metadata and bound image dimensions before sharing a photo."""
    try:
        with Image.open(photo_storage.open(storage_key)) as original:
            thumbnail = ImageOps.exif_transpose(original).convert("RGB")
            thumbnail.thumbnail((1200, 1200))
            output = BytesIO()
            thumbnail.save(output, format="JPEG", quality=85)
    except (OSError, ValueError):
        raise HTTPException(404, "Foto não disponível.")
    return output.getvalue()
