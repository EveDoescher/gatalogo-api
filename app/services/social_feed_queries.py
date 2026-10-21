from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import (
    HTTPException,
    Response,
)
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import (
    FeedItem,
    MissingReport,
    ProductState,
    ProgressEvent,
    RemoteCat,
    User,
)
from app.schemas import (
    FeedItemResponse,
)
from app.services.audience import can_read_post, permits
from app.services.conversations import friend_ids as _friend_ids
from app.services.social_graph import OFFICIAL_USER_ID as OFFICIAL_USER_ID
from app.services.social_presenter import _user_avatar_url
from app.services.storage_service import photo_storage


async def community_reports(*, user: User, session: AsyncSession):
    rows = (
        await session.execute(
            select(MissingReport, RemoteCat, User.username)
            .join(RemoteCat, RemoteCat.id == MissingReport.cat_id)
            .join(User, User.id == MissingReport.user_id)
            .where(MissingReport.status == "active", RemoteCat.deleted_at.is_(None))
            .order_by(MissingReport.created_at.desc())
            .limit(50)
        )
    ).all()
    return [
        {
            "id": str(report.id),
            "name": cat.payload.get("name") or "Gato desaparecido",
            "author": username or "Tutor",
            "is_owner": report.user_id == user.id,
            "status": report.status,
            "photo_url": f"/app/reports/{report.id}/photo",
        }
        for report, cat, username in rows
    ]


async def community_report(*, report_id: UUID, user: User, session: AsyncSession):
    report = await session.get(MissingReport, report_id)
    cat = await session.get(RemoteCat, report.cat_id) if report else None
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(404, "Busca não disponível.")
    owner = await session.get(User, report.user_id)
    from app.services.missing_posters import public_poster_details

    details = report.poster_details or {}
    return {
        **public_poster_details(report, cat),
        "id": str(report.id),
        "status": report.status,
        "is_owner": user.id == report.user_id,
        "name": cat.payload.get("name") or "Gato desaparecido",
        "author": owner.username or "Tutor",
        "description": details.get("description"),
        "photo_url": f"/app/reports/{report.id}/photo"
        if report.status == "active"
        else None,
        "message": "As pistas são avaliadas pelo tutor. As localizações dos colaboradores são privadas.",
    }


async def gallery_photo(*, cat_id: UUID, user: User, session: AsyncSession):
    cat = await session.get(RemoteCat, cat_id)
    if (
        cat is None
        or cat.deleted_at is not None
        or (not cat.photo_key)
        or (not await permits(session, user.id, cat.user_id, "gallery"))
    ):
        raise HTTPException(404, "Foto não disponível.")
    from io import BytesIO
    from PIL import Image, ImageOps

    with Image.open(photo_storage.open(cat.photo_key)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1200, 1200))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
    return Response(
        output.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


async def feed(
    *,
    limit: int,
    cursor: datetime | None,
    cursor_id: UUID | None,
    mode: str,
    user: User,
    session: AsyncSession,
) -> list[FeedItemResponse]:
    parts = mode.split(":", 1)
    mode = parts[0]
    content_type = parts[1] if len(parts) > 1 else "all"
    query = (
        select(FeedItem, User.username)
        .join(User, User.id == FeedItem.user_id)
        .where(FeedItem.user_id != OFFICIAL_USER_ID, User.username.is_not(None))
    )
    if content_type in {"cat_post", "achievement", "poster_repost"}:
        query = query.where(FeedItem.type == content_type)
    if mode == "friends":
        friend_ids = await _friend_ids(session, user.id)
        friend_ids.add(user.id)
        query = query.where(FeedItem.user_id.in_(friend_ids))
    else:
        friend_ids = await _friend_ids(session, user.id)
        friend_ids.add(user.id)
        query = query.where(
            or_(
                FeedItem.user_id.in_(friend_ids),
                FeedItem.type.in_(["cat_post", "achievement", "tip", "poster_repost"]),
            )
        )
    if cursor is not None:
        query = query.where(
            or_(
                FeedItem.created_at < cursor,
                and_(FeedItem.created_at == cursor, FeedItem.id < cursor_id),
            )
            if cursor_id is not None
            else FeedItem.created_at < cursor
        )
    result = []
    scan = query
    chunk_size = max(limit, 20)
    while len(result) < limit:
        items = (
            await session.execute(
                scan.order_by(FeedItem.created_at.desc(), FeedItem.id.desc()).limit(
                    chunk_size
                )
            )
        ).all()
        if not items:
            break
        for item, username in items:
            if not username or not await can_read_post(session, item, user.id):
                continue
            p = dict(item.payload or {})
            if item.type == "poster_repost":
                report = await session.get(MissingReport, UUID(p["report_id"]))
                p["report_status"] = report.status if report else "closed"
                p["can_repost"] = (
                    report is not None
                    and report.status == "active"
                    and (report.user_id != user.id)
                )
            likes = list(p.get("likes", []))
            p["likes_count"] = len(likes)
            p["has_liked"] = str(user.id) in likes
            p["comments"] = list(p.get("comments", []))
            pstate = await session.get(ProductState, item.user_id)
            p["author_avatar_url"] = _user_avatar_url(
                item.user_id, pstate.preferences if pstate else None
            )
            result.append(
                FeedItemResponse(
                    id=item.id,
                    username=username,
                    type=item.type,
                    payload=p,
                    created_at=item.created_at,
                )
            )
            if len(result) == limit:
                break
        if len(items) < chunk_size:
            break
        last = items[-1][0]
        scan = query.where(
            or_(
                FeedItem.created_at < last.created_at,
                and_(FeedItem.created_at == last.created_at, FeedItem.id < last.id),
            )
        )
    return result


async def get_post_photo(
    *, filename: str, user: User, session: AsyncSession
) -> Response:
    if Path(filename).name != filename:
        raise HTTPException(404, "Foto não encontrada.")
    try:
        photo_id = UUID(Path(filename).stem)
    except ValueError:
        raise HTTPException(404, "Foto não encontrada.")
    url = f"/social/posts/photo/{filename}"
    upload = await session.scalar(
        select(ProgressEvent).where(
            ProgressEvent.user_id == user.id,
            ProgressEvent.event_key == f"media:{photo_id}",
        )
    )
    posts = (
        await session.scalars(
            select(FeedItem).where(FeedItem.payload["photo_url"].as_string() == url)
        )
    ).all()
    allowed = upload is not None
    for post in posts:
        allowed = allowed or await can_read_post(session, post, user.id)
    if not allowed:
        raise HTTPException(404, "Foto não disponível.")
    files = list(
        photo_storage.absolute_path("posts").glob(
            f"{photo_id}-*{Path(filename).suffix}"
        )
    )
    if not files:
        raise HTTPException(404, "Foto não encontrada.")
    photo_file = files[0]
    from io import BytesIO
    from PIL import Image, ImageOps

    with Image.open(photo_file) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1600, 1600))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
    return Response(
        output.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )
