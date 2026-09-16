from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.services.auth_service import authenticated_user
from app.persistence_models import (
    AppNotification,
    Conversation,
    ConversationMember,
    ConversationMessage,
    DeviceToken,
    FeedItem,
    FriendInvite,
    Friendship,
    ProductState,
    User,
)
from app.routers.auth import current_user
from app.schemas import (
    ConversationSummaryResponse,
    ConversationUserResponse,
    DeviceTokenRequest,
    FeedItemResponse,
    FriendInviteCreate,
    FriendInviteResponse,
    MessageCreate,
    MessageEditRequest,
    MessageReactionRequest,
    MessageResponse,
    PublicUserResponse,
)
from app.services.realtime_events import (
    publish_committed_events,
    queue_event,
    queue_notification,
)
from app.services.storage_service import photo_storage
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/social", tags=["social"])


def _public(user: User) -> PublicUserResponse:
    if not user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Escolha um nome de usuário para usar recursos sociais.")
    return PublicUserResponse(id=user.id, username=user.username)


async def _friend_ids(session: AsyncSession, user_id: UUID) -> set[UUID]:
    rows = await session.execute(select(Friendship.friend_id).where(Friendship.user_id == user_id))
    return set(rows.scalars().all())


async def _member_or_403(session: AsyncSession, conversation_id: UUID, user_id: UUID) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada.")
    member = (await session.execute(select(ConversationMember.id).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == user_id))).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Você não participa desta conversa.")
    if conv.match_suggestion_id is None:
        others = (await session.execute(select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id != user_id))).scalars().all()
        if not others or not set(others).issubset(await _friend_ids(session, user_id)):
            raise HTTPException(403, "As conversas estão disponíveis entre amigos.")
    return conv


@router.get("/users/{username}", response_model=PublicUserResponse)
async def find_user(username: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> PublicUserResponse:
    target = (await session.execute(select(User).where(User.username == username.lower(), User.status == "active"))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    return _public(target)


@router.post("/invites", response_model=FriendInviteResponse, status_code=status.HTTP_201_CREATED)
async def send_invite(payload: FriendInviteCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FriendInviteResponse:
    sender = _public(user)
    recipient = (await session.execute(select(User).where(User.username == payload.username.lower(), User.status == "active"))).scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    if recipient.id == user.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Você não pode adicionar a si mesma.")
    if recipient.id in await _friend_ids(session, user.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Vocês já são amigos.")
    reverse = (await session.execute(select(FriendInvite).where(FriendInvite.sender_id == recipient.id, FriendInvite.recipient_id == user.id, FriendInvite.status == "pending"))).scalar_one_or_none()
    if reverse is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Você já recebeu um convite desta pessoa. Aceite ou recuse-o primeiro.")
    invite = (await session.execute(select(FriendInvite).where(FriendInvite.sender_id == user.id, FriendInvite.recipient_id == recipient.id))).scalar_one_or_none()
    notify_invite = invite is None or invite.status != "pending"
    if invite is None:
        invite = FriendInvite(sender_id=user.id, recipient_id=recipient.id, status="pending")
        session.add(invite)
    elif invite.status != "pending":
        invite.status = "pending"
        invite.responded_at = None
    if notify_invite:
        notification = AppNotification(
            user_id=recipient.id,
            type="friend_invite",
            payload={"username": user.username},
        )
        session.add(notification)
        await session.flush()
        queue_notification(session, notification)
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(invite)
    return FriendInviteResponse(id=invite.id, sender=sender, recipient=_public(recipient), status=invite.status, created_at=invite.created_at)


@router.get("/invites", response_model=list[FriendInviteResponse])
async def list_invites(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[FriendInviteResponse]:
    invites = (await session.execute(select(FriendInvite).where(or_(FriendInvite.sender_id == user.id, FriendInvite.recipient_id == user.id)).order_by(FriendInvite.created_at.desc()))).scalars().all()
    result: list[FriendInviteResponse] = []
    for invite in invites:
        sender = await session.get(User, invite.sender_id)
        recipient = await session.get(User, invite.recipient_id)
        if sender and recipient and sender.username and recipient.username:
            result.append(FriendInviteResponse(id=invite.id, sender=_public(sender), recipient=_public(recipient), status=invite.status, created_at=invite.created_at))
    return result


@router.post("/invites/{invite_id}/{decision}", response_model=FriendInviteResponse)
async def respond_invite(invite_id: UUID, decision: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FriendInviteResponse:
    if decision not in {"accept", "reject"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Resposta inválida.")
    invite = (await session.execute(select(FriendInvite).where(FriendInvite.id == invite_id, FriendInvite.recipient_id == user.id).with_for_update())).scalar_one_or_none()
    if invite is None or invite.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convite pendente não encontrado.")
    invite.status = "accepted" if decision == "accept" else "rejected"
    invite.responded_at = datetime.now(UTC)
    if decision == "accept":
        session.add_all((Friendship(user_id=invite.sender_id, friend_id=invite.recipient_id), Friendship(user_id=invite.recipient_id, friend_id=invite.sender_id)))
        notification = AppNotification(
            user_id=invite.sender_id,
            type="friend_accepted",
            payload={"username": user.username},
        )
        session.add(notification)
        await session.flush()
        queue_notification(session, notification)
    await session.commit()
    await publish_committed_events(session)
    sender = await session.get(User, invite.sender_id)
    return FriendInviteResponse(id=invite.id, sender=_public(sender), recipient=_public(user), status=invite.status, created_at=invite.created_at)


@router.get("/friends", response_model=list[PublicUserResponse])
async def list_friends(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[PublicUserResponse]:
    friends = (await session.execute(select(User).join(Friendship, Friendship.friend_id == User.id).where(Friendship.user_id == user.id).order_by(User.username))).scalars().all()
    return [_public(friend) for friend in friends if friend.username]


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(friend_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    rows = (await session.execute(select(Friendship).where(or_((Friendship.user_id == user.id) & (Friendship.friend_id == friend_id), (Friendship.user_id == friend_id) & (Friendship.friend_id == user.id))))).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.commit()


class CatPostCreate(BaseModel):
    cat_name: str
    note: str = ""
    location_name: str | None = None
    photo_base64: str | None = None
    photo_url: str | None = None
    is_public: bool = True


class CommentCreate(BaseModel):
    text: str


OFFICIAL_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


async def _ensure_official_tips(session: AsyncSession) -> None:
    oficial_user = await session.get(User, OFFICIAL_USER_ID)
    if not oficial_user:
        oficial_user = User(
            id=OFFICIAL_USER_ID,
            email="oficial@gatalogo.app",
            username="gatalogo_oficial",
            password_hash=None,
            status="active",
        )
        session.add(oficial_user)
        await session.flush()

    existing = (
        await session.execute(
            select(FeedItem.id).where(FeedItem.user_id == OFFICIAL_USER_ID)
        )
    ).scalars().all()
    if not existing:
        tip1 = FeedItem(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            user_id=OFFICIAL_USER_ID,
            type="tip",
            payload={
                "title": "Boas-vindas ao Gatálogo!",
                "note": "Você agora faz parte da rede de proteção felina! Fotografe qualquer gato na rua ou em casa para descobrir pelagem, padrão e registrar em sua coleção.",
                "tip_type": "welcome",
                "cat_name": "Primeiros Passos",
                "action_text": "Fotografar Primeiro Gato",
                "action_route": "camera",
                "likes": [],
                "likes_count": 0,
                "has_liked": False,
                "comments": [],
            },
        )
        tip2 = FeedItem(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            user_id=OFFICIAL_USER_ID,
            type="tip",
            payload={
                "title": "Dica de IA: Enquadramento Preciso",
                "note": "Para obter a maior precisão no reconhecimento facial e de pelagem do gato, procure boa iluminação e capture o corpo inteiro ou o rosto de frente.",
                "tip_type": "camera_tip",
                "cat_name": "Dica de Fotografia",
                "action_text": "Ver Minha Biblioteca",
                "action_route": "library",
                "likes": [],
                "likes_count": 0,
                "has_liked": False,
                "comments": [],
            },
        )
        tip3 = FeedItem(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            user_id=OFFICIAL_USER_ID,
            type="tip",
            payload={
                "title": "Rede Comunitária de Resgate",
                "note": "Se um gato for dado como perdido no seu bairro, um alerta será emitido no mapa. Caso você aviste o felino, envie uma foto e a localização exata pelo chat de resgate!",
                "tip_type": "rescue_tip",
                "cat_name": "Alerta e Proteção",
                "action_text": "Explorar Mapa",
                "action_route": "map",
                "likes": [],
                "likes_count": 0,
                "has_liked": False,
                "comments": [],
            },
        )
        session.add_all([tip1, tip2, tip3])
        await session.flush()


@router.get("/feed", response_model=list[FeedItemResponse])
async def feed(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    mode: str = Query(default="all"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[FeedItemResponse]:
    await _ensure_official_tips(session)

    query = select(FeedItem, User.username).join(User, User.id == FeedItem.user_id)

    if mode == "friends":
        friend_ids = await _friend_ids(session, user.id)
        friend_ids.add(user.id)
        friend_ids.add(OFFICIAL_USER_ID)
        query = query.where(FeedItem.user_id.in_(friend_ids))
    else:
        friend_ids = await _friend_ids(session, user.id)
        friend_ids.add(user.id)
        friend_ids.add(OFFICIAL_USER_ID)
        query = query.where(
            or_(
                FeedItem.user_id.in_(friend_ids),
                FeedItem.type.in_(["cat_post", "achievement", "tip"]),
            )
        )

    if cursor is not None:
        query = query.where(FeedItem.created_at < cursor)

    items = (
        await session.execute(
            query.order_by(FeedItem.created_at.desc()).limit(limit)
        )
    ).all()

    result = []
    for item, username in items:
        if not username:
            continue
        p = dict(item.payload or {})
        likes = list(p.get("likes", []))
        p["likes_count"] = len(likes)
        p["has_liked"] = str(user.id) in likes
        p["comments"] = list(p.get("comments", []))
        result.append(
            FeedItemResponse(
                id=item.id,
                username=username,
                type=item.type,
                payload=p,
                created_at=item.created_at,
            )
        )
    return result


@router.post("/posts/photo")
async def upload_post_photo(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Arquivo vazio.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Arquivo muito grande (máximo 15MB).")

    suffix = Path(file.filename or "photo.jpg").suffix or ".jpg"
    photo_id = uuid4()
    key, _ = photo_storage.save_post_photo(
        post_id=photo_id,
        data=data,
        suffix=suffix,
    )
    photo_url = f"/social/posts/photo/{photo_id}{suffix}"
    return {"photo_url": photo_url, "photo_key": key}


@router.get("/posts/photo/{filename}")
async def get_post_photo(
    filename: str,
) -> Response:
    folder = photo_storage.absolute_path("posts")
    if not folder.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Foto não encontrada.")
    matches = list(folder.glob(f"{Path(filename).stem}*"))
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Foto não encontrada.")
    photo_file = matches[0]
    return FileResponse(
        photo_file,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.post("/posts", response_model=FeedItemResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: CatPostCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> FeedItemResponse:
    if not user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Escolha um nome de usuário para postar no feed.")
    cat_name = payload.cat_name.strip()
    if not cat_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="O nome do gato é obrigatório.")

    photo_url = payload.photo_url
    if not photo_url and payload.photo_base64:
        try:
            import base64
            raw_bytes = base64.b64decode(payload.photo_base64)
            if raw_bytes:
                pid = uuid4()
                photo_storage.save_post_photo(post_id=pid, data=raw_bytes, suffix=".jpg")
                photo_url = f"/social/posts/photo/{pid}.jpg"
        except Exception:
            pass

    item_payload = {
        "cat_name": cat_name,
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
    return FeedItemResponse(id=item.id, username=user.username, type=item.type, payload=item.payload or {}, created_at=item.created_at)


@router.post("/feed/{item_id}/like")
async def toggle_like(item_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    item = await session.get(FeedItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item do feed não encontrado.")
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
async def add_comment(item_id: UUID, payload: CommentCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[dict]:
    item = await session.get(FeedItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item do feed não encontrado.")
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="O comentário não pode ser vazio.")
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


@router.put("/device-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(payload: DeviceTokenRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    existing = (await session.execute(select(DeviceToken).where(DeviceToken.token == payload.token).with_for_update())).scalar_one_or_none()
    if existing is None:
        session.add(DeviceToken(user_id=user.id, token=payload.token, platform=payload.platform, preferences=payload.preferences))
    else:
        existing.user_id, existing.platform, existing.preferences = user.id, payload.platform, payload.preferences
    await session.commit()


def _build_message_response(
    row: ConversationMessage,
    current_user_id: UUID,
    reply_to_row: ConversationMessage | None = None,
) -> MessageResponse:
    reply_dict = None
    if reply_to_row:
        reply_dict = {
            "id": reply_to_row.id,
            "sender_id": reply_to_row.sender_id,
            "body": "Mensagem apagada" if reply_to_row.is_deleted else reply_to_row.body,
            "type": reply_to_row.type,
        }
    return MessageResponse(
        id=row.id,
        conversation_id=row.conversation_id,
        sender_id=row.sender_id,
        body="Mensagem apagada" if row.is_deleted else row.body,
        type=row.type,
        media_url=None if row.is_deleted else row.media_url,
        location_lat=None if row.is_deleted else row.location_lat,
        location_lng=None if row.is_deleted else row.location_lng,
        location_title=None if row.is_deleted else row.location_title,
        cat_data=None if row.is_deleted else row.cat_data,
        read_at=row.read_at,
        is_read=row.read_at is not None,
        reply_to_id=row.reply_to_id,
        reply_to=reply_dict,
        reactions=row.reactions or {},
        is_edited=row.is_edited,
        is_deleted=row.is_deleted,
        created_at=row.created_at,
    )


def _user_avatar_url(user_id: UUID, preferences: dict | None) -> str | None:
    avatar_url = (preferences or {}).get("avatar_url") if preferences else None
    if not avatar_url:
        return None
    try:
        path = photo_storage.absolute_path(photo_storage.profile_key_for(user_id, ".jpg"))
        if path.parent.is_dir() and any(path.parent.glob("avatar*")):
            return avatar_url
    except Exception:
        pass
    return None


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
async def conversations_list(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[ConversationSummaryResponse]:
    my_memberships = (await session.execute(
        select(ConversationMember.conversation_id)
        .where(ConversationMember.user_id == user.id)
    )).scalars().all()

    if not my_memberships:
        return []

    conversations = (await session.execute(
        select(Conversation)
        .where(Conversation.id.in_(my_memberships))
    )).scalars().all()

    results: list[ConversationSummaryResponse] = []
    for conv in conversations:
        other_member = (await session.execute(
            select(User, ProductState.preferences)
            .join(ConversationMember, ConversationMember.user_id == User.id)
            .outerjoin(ProductState, ProductState.user_id == User.id)
            .where(ConversationMember.conversation_id == conv.id, User.id != user.id)
        )).first()

        if not other_member:
            continue
        other_user, preferences = other_member
        avatar_url = _user_avatar_url(other_user.id, preferences)

        is_online = ws_manager.is_online(other_user.id)
        last_seen = ws_manager.get_last_seen(other_user.id) or other_user.last_seen_at

        last_msg = (await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conv.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        unread_count = (await session.execute(
            select(func.count())
            .select_from(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conv.id,
                ConversationMessage.sender_id != user.id,
                ConversationMessage.read_at.is_(None),
            )
        )).scalar_one()

        last_msg_resp = _build_message_response(last_msg, user.id) if last_msg else None
        updated_at = last_msg.created_at if last_msg else conv.created_at

        results.append(
            ConversationSummaryResponse(
                id=conv.id,
                other_user=ConversationUserResponse(
                    id=other_user.id,
                    username=other_user.username or "amigo",
                    avatar_url=avatar_url,
                    is_online=is_online,
                    last_seen_at=last_seen,
                ),
                last_message=last_msg_resp,
                unread_count=unread_count,
                sighting_context=conv.match_suggestion_id is not None,
                updated_at=updated_at,
            )
        )

    results.sort(key=lambda c: c.updated_at, reverse=True)
    return results


@router.get("/conversations/{conversation_id}")
async def conversation_details(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    conv = await _member_or_403(session, conversation_id, user.id)
    other_member = (await session.execute(
        select(User, ProductState.preferences)
        .join(ConversationMember, ConversationMember.user_id == User.id)
        .outerjoin(ProductState, ProductState.user_id == User.id)
        .where(ConversationMember.conversation_id == conversation_id, User.id != user.id)
    )).first()

    other_user, preferences = other_member if other_member else (None, None)
    avatar_url = _user_avatar_url(other_user.id, preferences) if other_user else None

    is_online = ws_manager.is_online(other_user.id) if other_user else False
    last_seen = (ws_manager.get_last_seen(other_user.id) or other_user.last_seen_at) if other_user else None

    return {
        "id": str(conv.id),
        "username": other_user.username if other_user else None,
        "other_user_id": str(other_user.id) if other_user else None,
        "avatar_url": avatar_url,
        "is_online": is_online,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "sighting_context": conv.match_suggestion_id is not None,
    }


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def messages(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[MessageResponse]:
    await _member_or_403(session, conversation_id, user.id)
    rows = (await session.execute(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at)
    )).scalars().all()

    reply_ids = {r.reply_to_id for r in rows if r.reply_to_id}
    replies: dict[UUID, ConversationMessage] = {}
    if reply_ids:
        reply_rows = (await session.execute(
            select(ConversationMessage).where(ConversationMessage.id.in_(reply_ids))
        )).scalars().all()
        replies = {r.id: r for r in reply_rows}

    return [_build_message_response(row, user.id, replies.get(row.reply_to_id)) for row in rows]


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> MessageResponse:
    await _member_or_403(session, conversation_id, user.id)
    body = (payload.body or "").strip()
    msg_type = payload.type or "text"

    if msg_type == "text" and not body:
        raise HTTPException(422, "Escreva uma mensagem.")
    elif msg_type == "photo" and not payload.media_url:
        raise HTTPException(422, "Foto não informada.")
    elif msg_type == "location" and (payload.location_lat is None or payload.location_lng is None):
        raise HTTPException(422, "Coordenadas de localização não informadas.")
    elif msg_type == "cat_card" and not payload.cat_data:
        raise HTTPException(422, "Dados do gato não informados.")

    message = ConversationMessage(
        conversation_id=conversation_id,
        sender_id=user.id,
        body=body,
        type=msg_type,
        media_url=payload.media_url,
        location_lat=payload.location_lat,
        location_lng=payload.location_lng,
        location_title=payload.location_title,
        cat_data=payload.cat_data,
        reply_to_id=payload.reply_to_id,
    )
    session.add(message)
    await session.flush()

    reply_row = None
    if message.reply_to_id:
        reply_row = await session.get(ConversationMessage, message.reply_to_id)

    resp = _build_message_response(message, user.id, reply_row)

    members = (await session.execute(
        select(ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id != user.id)
    )).scalars().all()

    for recipient_id in members:
        notification = AppNotification(
            user_id=recipient_id,
            type="message",
            payload={
                "conversation_id": str(conversation_id),
                "username": user.username,
                "body": body[:100] if body else f"[{msg_type}]",
            },
        )
        session.add(notification)
        await session.flush()
        queue_notification(session, notification)
        queue_event(
            session,
            user_id=recipient_id,
            event="new_message",
            data={
                "conversation_id": str(conversation_id),
                "message": resp.model_dump(mode="json"),
            },
        )
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(message)
    return resp


@router.post("/conversations/{conversation_id}/photo")
async def upload_chat_photo(
    conversation_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    await _member_or_403(session, conversation_id, user.id)
    data = await file.read()
    if not data:
        raise HTTPException(422, "Arquivo vazio.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Arquivo muito grande (máximo 15MB).")

    suffix = Path(file.filename or "photo.jpg").suffix or ".jpg"
    msg_id = uuid4()
    key, content_hash = photo_storage.save_chat_photo(
        conversation_id=conversation_id,
        message_id=msg_id,
        data=data,
        suffix=suffix,
    )
    media_url = f"/social/conversations/{conversation_id}/photo/{msg_id}{suffix}"
    return {"media_url": media_url, "photo_key": key}


@router.get("/conversations/{conversation_id}/photo/{filename}")
async def get_chat_photo(
    conversation_id: UUID,
    filename: str,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_database_session),
) -> Response:
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.removeprefix("Bearer ")
    elif token:
        raw_token = token
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de autorização não fornecido.")
    user = await authenticated_user(raw_token, session)
    await _member_or_403(session, conversation_id, user.id)
    folder = photo_storage.absolute_path(str(Path("conversations") / str(conversation_id)))
    if not folder.exists():
        raise HTTPException(404, "Foto não encontrada.")
    matches = list(folder.glob(f"{Path(filename).stem}*"))
    if not matches:
        raise HTTPException(404, "Foto não encontrada.")
    photo_file = matches[0]
    return FileResponse(photo_file, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    await _member_or_403(session, conversation_id, user.id)
    now = datetime.now(UTC)
    messages_to_mark = (await session.execute(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.sender_id != user.id,
            ConversationMessage.read_at.is_(None),
        )
    )).scalars().all()

    for m in messages_to_mark:
        m.read_at = now
    if messages_to_mark:
        await session.commit()

    members = (await session.execute(
        select(ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id != user.id)
    )).scalars().all()

    for recipient_id in members:
        queue_event(
            session,
            user_id=recipient_id,
            event="messages_read",
            data={
                "conversation_id": str(conversation_id),
                "reader_id": str(user.id),
                "read_at": now.isoformat(),
            },
        )
    await publish_committed_events(session)
    return {"status": "ok", "marked_count": len(messages_to_mark)}


@router.post("/conversations/{conversation_id}/messages/{message_id}/reactions")
async def toggle_message_reaction(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageReactionRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    await _member_or_403(session, conversation_id, user.id)
    message = await session.get(ConversationMessage, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(404, "Mensagem não encontrada.")

    reactions = dict(message.reactions or {})
    emoji = payload.emoji.strip()
    user_id_str = str(user.id)
    users_list = list(reactions.get(emoji, []))

    if user_id_str in users_list:
        users_list.remove(user_id_str)
        if not users_list:
            reactions.pop(emoji, None)
        else:
            reactions[emoji] = users_list
    else:
        users_list.append(user_id_str)
        reactions[emoji] = users_list

    message.reactions = reactions
    await session.commit()

    members = (await session.execute(
        select(ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation_id)
    )).scalars().all()
    for recipient_id in members:
        queue_event(
            session,
            user_id=recipient_id,
            event="message_reaction",
            data={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "reactions": reactions,
            },
        )
    await publish_committed_events(session)
    return {"reactions": reactions}


@router.put("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
async def edit_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageEditRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> MessageResponse:
    await _member_or_403(session, conversation_id, user.id)
    message = await session.get(ConversationMessage, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(404, "Mensagem não encontrada.")
    if message.sender_id != user.id:
        raise HTTPException(403, "Você só pode editar suas próprias mensagens.")
    if message.is_deleted:
        raise HTTPException(400, "Mensagem já foi apagada.")

    message.body = payload.body.strip()
    message.is_edited = True
    await session.commit()
    await session.refresh(message)

    resp = _build_message_response(message, user.id)
    members = (await session.execute(
        select(ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation_id)
    )).scalars().all()
    for recipient_id in members:
        queue_event(
            session,
            user_id=recipient_id,
            event="message_edited",
            data={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "message": resp.model_dump(mode="json"),
            },
        )
    await publish_committed_events(session)
    return resp


@router.delete("/conversations/{conversation_id}/messages/{message_id}")
async def delete_message(
    conversation_id: UUID,
    message_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    await _member_or_403(session, conversation_id, user.id)
    message = await session.get(ConversationMessage, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(404, "Mensagem não encontrada.")
    if message.sender_id != user.id:
        raise HTTPException(403, "Você só pode apagar suas próprias mensagens.")

    message.is_deleted = True
    message.body = "Mensagem apagada"
    message.media_url = None
    message.location_lat = None
    message.location_lng = None
    message.location_title = None
    message.cat_data = None
    await session.commit()

    members = (await session.execute(
        select(ConversationMember.user_id)
        .where(ConversationMember.conversation_id == conversation_id)
    )).scalars().all()
    for recipient_id in members:
        queue_event(
            session,
            user_id=recipient_id,
            event="message_deleted",
            data={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
            },
        )
    await publish_committed_events(session)
    return {"status": "deleted"}


@router.get("/users/{user_id}/presence")
async def user_presence(
    user_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    target = await session.get(User, user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado.")
    is_online = ws_manager.is_online(user_id)
    last_seen = ws_manager.get_last_seen(user_id) or target.last_seen_at
    return {
        "user_id": str(user_id),
        "is_online": is_online,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
    }
