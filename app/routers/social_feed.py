from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.dependencies import current_user
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
from app.services import progression as progress
from app.services.audience import can_read_post, permits
from app.services.conversations import friend_ids as _friend_ids
from app.services.realtime_events import (
    publish_committed_events,
    queue_event,
)
from app.services.social_graph import OFFICIAL_USER_ID as OFFICIAL_USER_ID
from app.services.social_presenter import _user_avatar_url
from app.services.storage_service import photo_storage

router = APIRouter(prefix="/social", tags=["social"])


class CatPostCreate(BaseModel):
    cat_name: str = Field(default="", max_length=100)
    request_id: UUID | None = None
    note: str = Field(default="", max_length=2000)
    location_name: str | None = None
    photo_base64: str | None = None
    photo_url: str | None = None
    is_public: bool = True


class CommentCreate(BaseModel):
    text: str


@router.get("/reports")
async def community_reports(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
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


@router.get("/reports/{report_id}")
async def community_report(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
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


@router.get("/gallery/{cat_id}/photo")
async def gallery_photo(
    cat_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    cat = await session.get(RemoteCat, cat_id)
    if (
        cat is None
        or cat.deleted_at is not None
        or not cat.photo_key
        or not await permits(session, user.id, cat.user_id, "gallery")
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


@router.post("/reports/{report_id}/repost", response_model=FeedItemResponse)
async def repost_report(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await _share_report(report_id, user, session, owner_publication=False)


@router.post("/reports/{report_id}/publish", response_model=FeedItemResponse)
async def publish_report(
    report_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
):
    return await _share_report(report_id, user, session, owner_publication=True)


async def _share_report(report_id, user, session, *, owner_publication):
    await progress.account(session, user.id)
    report = await session.get(MissingReport, report_id, with_for_update=True)
    if report is None or report.status != "active":
        raise HTTPException(409, "Este cartaz não está mais ativo.")
    if owner_publication and report.user_id != user.id:
        raise HTTPException(404, "Alerta não encontrado.")
    if not owner_publication and report.user_id == user.id:
        raise HTTPException(
            422, "Use a publicação do seu cartaz para divulgar sua busca."
        )
    previous = await session.scalar(
        select(ProgressEvent).where(
            ProgressEvent.user_id == user.id,
            ProgressEvent.event_key == f"repost:{report.id}",
        )
    )
    if previous:
        item = await session.get(FeedItem, UUID(previous.record_id))
    else:
        cat = await session.get(RemoteCat, report.cat_id)
        owner = await session.get(User, report.user_id)
        item = FeedItem(
            user_id=user.id,
            type="poster_repost",
            payload={
                "report_id": str(report.id),
                "original_author": owner.username or "Tutor",
                "cat_name": cat.payload.get("name") or "Gato desaparecido",
                "note": "Ajude esta busca. Uma pista útil pode fazer a diferença.",
                "photo_url": f"/app/reports/{report.id}/photo",
                "is_public": True,
                "report_status": "active",
                "likes": [],
                "comments": [],
            },
        )
        session.add(item)
        await session.flush()
        event = await progress.grant(
            session,
            user.id,
            f"repost:{report.id}",
            "post" if owner_publication else "repost",
            record_id=str(item.id),
            evidence={
                "report_id": str(report.id),
                "original_author_id": str(report.user_id),
            },
        )
        await progress.finish(session, event)
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(item)
    return FeedItemResponse(
        id=item.id,
        username=user.username or "Amigo",
        type=item.type,
        payload=item.payload,
        created_at=item.created_at,
    )


@router.get("/feed", response_model=list[FeedItemResponse])
async def feed(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    cursor_id: UUID | None = Query(default=None),
    mode: str = Query(default="all"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
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

    # Filtered/private rows must not make a partial page look like end of feed.
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
                    and report.user_id != user.id
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


@router.post("/posts/photo")
async def upload_post_photo(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Arquivo vazio.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Arquivo muito grande (máximo 15MB).",
        )

    suffix = Path(file.filename or "photo.jpg").suffix or ".jpg"
    photo_id = uuid4()
    key, _ = photo_storage.save_post_photo(
        post_id=photo_id,
        data=data,
        suffix=suffix,
    )
    photo_url = f"/social/posts/photo/{photo_id}{suffix}"
    session.add(
        ProgressEvent(
            user_id=user.id,
            event_key=f"media:{photo_id}",
            kind="media_upload",
            state="confirmed",
            xp=0,
            coins=0,
            evidence={"photo_url": photo_url},
            result={},
        )
    )
    await session.commit()
    return {"photo_url": photo_url, "photo_key": key}


@router.get("/posts/photo/{filename}")
async def get_post_photo(
    filename: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
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


@router.post(
    "/posts", response_model=FeedItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_post(
    payload: CatPostCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> FeedItemResponse:
    await progress.account(session, user.id)
    if not user.username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Escolha um nome de usuário para postar no feed.",
        )
    cat_name = payload.cat_name.strip()
    if not payload.note.strip() and not payload.photo_url and not payload.photo_base64:
        raise HTTPException(422, "Escreva algo ou adicione uma foto.")
    request_content = hashlib.sha256(
        json.dumps(
            {
                "cat_name": cat_name,
                "note": payload.note.strip(),
                "location_name": payload.location_name,
                "photo_url": payload.photo_url,
                "photo_base64": payload.photo_base64,
                "is_public": payload.is_public,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if payload.request_id:
        await session.scalar(select(User).where(User.id == user.id).with_for_update())
        previous = await session.scalar(
            select(FeedItem).where(
                FeedItem.user_id == user.id,
                FeedItem.payload["request_id"].as_string() == str(payload.request_id),
            )
        )
        if previous is not None:
            if previous.payload.get("request_content") != request_content:
                raise HTTPException(
                    409,
                    "Esta tentativa já publicou outro conteúdo. Inicie uma nova publicação.",
                )
            return FeedItemResponse(
                id=previous.id,
                username=user.username,
                type=previous.type,
                payload=previous.payload or {},
                created_at=previous.created_at,
            )

    photo_url = payload.photo_url
    if photo_url:
        owned_upload = await session.scalar(
            select(ProgressEvent).where(
                ProgressEvent.user_id == user.id,
                ProgressEvent.kind == "media_upload",
                ProgressEvent.evidence["photo_url"].as_string() == photo_url,
            )
        )
        if owned_upload is None:
            raise HTTPException(
                422, "Envie uma foto da sua conta para esta publicação."
            )
    if not photo_url and payload.photo_base64:
        try:
            import base64

            raw_bytes = base64.b64decode(payload.photo_base64, validate=True)
            if raw_bytes:
                pid = uuid4()
                photo_storage.save_post_photo(
                    post_id=pid, data=raw_bytes, suffix=".jpg"
                )
                photo_url = f"/social/posts/photo/{pid}.jpg"
        except Exception as error:
            raise HTTPException(
                422, "Não foi possível processar a foto. Envie-a novamente."
            ) from error
    if not photo_url and not payload.note.strip():
        raise HTTPException(422, "Escreva algo ou adicione uma foto válida.")

    item_payload = {
        "cat_name": cat_name,
        "request_id": str(payload.request_id) if payload.request_id else None,
        "request_content": request_content,
        "note": payload.note.strip(),
        "location_name": payload.location_name,
        "photo_url": photo_url,
        "photo_base64": payload.photo_base64 if not photo_url else None,
        "is_public": payload.is_public,
        "likes": [],
        "likes_count": 0,
        "has_liked": False,
        "comments": [],
    }
    item = FeedItem(user_id=user.id, type="cat_post", payload=item_payload)
    session.add(item)
    await session.flush()
    fingerprint = hashlib.sha256(
        (payload.note.strip().casefold() + "|" + cat_name.casefold()).encode()
    ).hexdigest()
    event = await progress.grant(
        session,
        user.id,
        f"post:{fingerprint}",
        "post",
        record_id=str(item.id),
        evidence={"eligible": len(payload.note.strip()) >= 8, "post_id": str(item.id)},
    )
    if not event.result:
        await progress.finish(session, event)
    friend_ids = await _friend_ids(session, user.id)
    for fid in friend_ids:
        queue_event(
            session,
            user_id=fid,
            event="feed_update",
            data={"type": "new_post", "item_id": str(item.id)},
        )
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(item)
    return FeedItemResponse(
        id=item.id,
        username=user.username,
        type=item.type,
        payload=item.payload or {},
        created_at=item.created_at,
    )


@router.post("/feed/{item_id}/like")
async def toggle_like(
    item_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    await progress.account(session, user.id)
    item = await session.get(FeedItem, item_id, with_for_update=True)
    if item is None or not await can_read_post(session, item, user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item do feed não encontrado."
        )
    item_payload = dict(item.payload or {})
    likes = list(item_payload.get("likes", []))
    uid_str = str(user.id)
    if uid_str in likes:
        likes.remove(uid_str)
        liked = False
    else:
        likes.append(uid_str)
        liked = True
    item_payload["likes"] = likes
    item_payload["likes_count"] = len(likes)
    item_payload["has_liked"] = liked
    item.payload = item_payload

    friend_ids = await _friend_ids(session, user.id)
    target_ids = friend_ids | {item.user_id}
    for tid in target_ids:
        queue_event(
            session,
            user_id=tid,
            event="feed_update",
            data={
                "type": "like_update",
                "item_id": str(item_id),
                "likes_count": len(likes),
                "user_id": str(user.id),
                "liked": liked,
            },
        )
    await session.commit()
    await publish_committed_events(session)
    return {"liked": liked, "likes_count": len(likes)}


@router.post("/feed/{item_id}/comments")
async def add_comment(
    item_id: UUID,
    payload: CommentCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[dict]:
    await progress.account(session, user.id)
    item = await session.get(FeedItem, item_id, with_for_update=True)
    if item is None or not await can_read_post(session, item, user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item do feed não encontrado."
        )
    text = payload.text.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="O comentário não pode ser vazio.",
        )
    item_payload = dict(item.payload or {})
    comments = list(item_payload.get("comments", []))
    comment = {
        "id": str(uuid4()),
        "user_id": str(user.id),
        "username": user.username or "amigo",
        "text": text,
        "created_at": datetime.now(UTC).isoformat(),
    }
    comments.append(comment)
    item_payload["comments"] = comments
    item.payload = item_payload

    if item.user_id != user.id:
        fingerprint = hashlib.sha256(text.casefold().encode()).hexdigest()
        event = await progress.grant(
            session,
            user.id,
            f"comment:{fingerprint}",
            "comment",
            record_id=str(item.id),
            evidence={"eligible": len(text) >= 8, "post_id": str(item.id)},
        )
        if not event.result:
            await progress.finish(session, event)

    friend_ids = await _friend_ids(session, user.id)
    target_ids = friend_ids | {item.user_id}
    for tid in target_ids:
        queue_event(
            session,
            user_id=tid,
            event="feed_update",
            data={
                "type": "new_comment",
                "item_id": str(item_id),
                "comment": comment,
                "comments_count": len(comments),
            },
        )
    await session.commit()
    await publish_committed_events(session)
    return comments
