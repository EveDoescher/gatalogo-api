from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.persistence_models import (
    AppNotification,
    Conversation,
    ConversationMember,
    ConversationMessage,
    DeviceToken,
    FeedItem,
    FriendInvite,
    Friendship,
    User,
)
from app.routers.auth import current_user
from app.schemas import (
    DeviceTokenRequest,
    FeedItemResponse,
    FriendInviteCreate,
    FriendInviteResponse,
    MessageCreate,
    MessageResponse,
    PublicUserResponse,
)
from app.services.realtime_events import (
    publish_committed_events,
    queue_event,
    queue_notification,
)

router = APIRouter(prefix="/social", tags=["social"])


def _public(user: User) -> PublicUserResponse:
    if not user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Escolha um nome de usuário para usar recursos sociais.")
    return PublicUserResponse(id=user.id, username=user.username)


async def _friend_ids(session: AsyncSession, user_id: UUID) -> set[UUID]:
    rows = await session.execute(select(Friendship.friend_id).where(Friendship.user_id == user_id))
    return set(rows.scalars().all())


async def _member_or_403(session: AsyncSession, conversation_id: UUID, user_id: UUID) -> None:
    member = (await session.execute(select(ConversationMember.id).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == user_id))).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Você não participa desta conversa.")
    others = (await session.execute(select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id != user_id))).scalars().all()
    if not others or not set(others).issubset(await _friend_ids(session, user_id)):
        raise HTTPException(403, "As conversas estão disponíveis entre amigos.")


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


class CommentCreate(BaseModel):
    text: str


@router.get("/feed", response_model=list[FeedItemResponse])
async def feed(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[FeedItemResponse]:
    ids = await _friend_ids(session, user.id)
    ids.add(user.id)
    items = (await session.execute(select(FeedItem, User.username).join(User, User.id == FeedItem.user_id).where(FeedItem.user_id.in_(ids)).order_by(FeedItem.created_at.desc()).limit(100))).all()
    result = []
    for item, username in items:
        if not username:
            continue
        p = dict(item.payload or {})
        likes = list(p.get("likes", []))
        p["likes_count"] = len(likes)
        p["has_liked"] = str(user.id) in likes
        p["comments"] = list(p.get("comments", []))
        result.append(FeedItemResponse(id=item.id, username=username, type=item.type, payload=p, created_at=item.created_at))
    return result


@router.post("/posts", response_model=FeedItemResponse, status_code=status.HTTP_201_CREATED)
async def create_post(payload: CatPostCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FeedItemResponse:
    if not user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Escolha um nome de usuário para postar no feed.")
    cat_name = payload.cat_name.strip()
    if not cat_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="O nome do gato é obrigatório.")
    item_payload = {
        "cat_name": cat_name,
        "note": payload.note.strip(),
        "location_name": payload.location_name,
        "photo_base64": payload.photo_base64,
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


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def messages(conversation_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[MessageResponse]:
    await _member_or_403(session, conversation_id, user.id)
    rows = (await session.execute(select(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id).order_by(ConversationMessage.created_at))).scalars().all()
    return [MessageResponse(id=row.id, sender_id=row.sender_id, body=row.body, created_at=row.created_at) for row in rows]


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(conversation_id: UUID, payload: MessageCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> MessageResponse:
    await _member_or_403(session, conversation_id, user.id)
    if not payload.body.strip():
        raise HTTPException(422, "Escreva uma mensagem.")
    message = ConversationMessage(conversation_id=conversation_id, sender_id=user.id, body=payload.body.strip())
    session.add(message)
    await session.flush()
    members = (await session.execute(select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id != user.id))).scalars().all()
    for recipient_id in members:
        notification = AppNotification(
            user_id=recipient_id,
            type="message",
            payload={"conversation_id": str(conversation_id), "username": user.username},
        )
        session.add(notification)
        await session.flush()
        queue_notification(session, notification)
        queue_event(
            session,
            user_id=recipient_id,
            event="new_message",
            data={"conversation_id": str(conversation_id)},
        )
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(message)
    return MessageResponse(id=message.id, sender_id=message.sender_id, body=message.body, created_at=message.created_at)
