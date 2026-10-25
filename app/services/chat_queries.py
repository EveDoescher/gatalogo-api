from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import (
    HTTPException,
    Response,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    ProductState,
    User,
)
from app.schemas import (
    ConversationSummaryResponse,
    ConversationUserResponse,
    MessageResponse,
)
from app.services.auth_service import authenticated_user
from app.services.chat_presenter import _build_message_response
from app.services.conversations import member_or_403 as _member_or_403
from app.services.social_presenter import _user_avatar_url
from app.services.storage_service import photo_storage
from app.services.websocket_manager import ws_manager


async def conversations_list(
    *, user: User, session: AsyncSession
) -> list[ConversationSummaryResponse]:
    my_memberships = (
        (
            await session.execute(
                select(ConversationMember.conversation_id).where(
                    ConversationMember.user_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not my_memberships:
        return []
    conversations = (
        (
            await session.execute(
                select(Conversation).where(Conversation.id.in_(my_memberships))
            )
        )
        .scalars()
        .all()
    )
    results: list[ConversationSummaryResponse] = []
    for conv in conversations:
        other_member = (
            await session.execute(
                select(User, ProductState.preferences)
                .join(ConversationMember, ConversationMember.user_id == User.id)
                .outerjoin(ProductState, ProductState.user_id == User.id)
                .where(
                    ConversationMember.conversation_id == conv.id, User.id != user.id
                )
            )
        ).first()
        if not other_member:
            continue
        other_user, preferences = other_member
        avatar_url = _user_avatar_url(other_user.id, preferences)
        is_online = ws_manager.is_online(other_user.id)
        last_seen = ws_manager.get_last_seen(other_user.id) or other_user.last_seen_at
        last_msg = (
            await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conv.id)
                .order_by(
                    ConversationMessage.created_at.desc(), ConversationMessage.id.desc()
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        unread_count = (
            await session.execute(
                select(func.count())
                .select_from(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conv.id,
                    ConversationMessage.sender_id != user.id,
                    ConversationMessage.read_at.is_(None),
                )
            )
        ).scalar_one()
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


async def conversation_details(
    *, conversation_id: UUID, user: User, session: AsyncSession
) -> dict:
    conv = await _member_or_403(session, conversation_id, user.id)
    other_member = (
        await session.execute(
            select(User, ProductState.preferences)
            .join(ConversationMember, ConversationMember.user_id == User.id)
            .outerjoin(ProductState, ProductState.user_id == User.id)
            .where(
                ConversationMember.conversation_id == conversation_id,
                User.id != user.id,
            )
        )
    ).first()
    other_user, preferences = other_member if other_member else (None, None)
    avatar_url = _user_avatar_url(other_user.id, preferences) if other_user else None
    is_online = ws_manager.is_online(other_user.id) if other_user else False
    last_seen = (
        ws_manager.get_last_seen(other_user.id) or other_user.last_seen_at
        if other_user
        else None
    )
    return {
        "id": str(conv.id),
        "username": other_user.username if other_user else None,
        "other_user_id": str(other_user.id) if other_user else None,
        "avatar_url": avatar_url,
        "is_online": is_online,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "sighting_context": conv.match_suggestion_id is not None,
    }


async def messages(
    *, conversation_id: UUID, user: User, session: AsyncSession
) -> list[MessageResponse]:
    await _member_or_403(session, conversation_id, user.id)
    rows = (
        (
            await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )
        )
        .scalars()
        .all()
    )
    reply_ids = {r.reply_to_id for r in rows if r.reply_to_id}
    replies: dict[UUID, ConversationMessage] = {}
    if reply_ids:
        reply_rows = (
            (
                await session.execute(
                    select(ConversationMessage).where(
                        ConversationMessage.id.in_(reply_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        replies = {r.id: r for r in reply_rows}
    return [
        _build_message_response(row, user.id, replies.get(row.reply_to_id))
        for row in rows
    ]


async def get_chat_photo(
    *,
    conversation_id: UUID,
    filename: str,
    token: str | None,
    authorization: str | None,
    session: AsyncSession,
) -> Response:
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.removeprefix("Bearer ")
    elif token:
        raw_token = token
    if not raw_token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Token de autorização não fornecido."
        )
    user = await authenticated_user(raw_token, session)
    await _member_or_403(session, conversation_id, user.id)
    folder = photo_storage.absolute_path(
        str(Path("conversations") / str(conversation_id))
    )
    if not folder.exists():
        raise HTTPException(404, "Foto não encontrada.")
    matches = list(folder.glob(f"{Path(filename).stem}*"))
    if not matches:
        raise HTTPException(404, "Foto não encontrada.")
    photo_file = matches[0]
    return FileResponse(
        photo_file,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


async def user_presence(*, user_id: UUID, user: User, session: AsyncSession) -> dict:
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
