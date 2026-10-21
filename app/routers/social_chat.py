from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.dependencies import current_user
from app.persistence_models import (
    AppNotification,
    Conversation,
    ConversationMember,
    ConversationMessage,
    ProductState,
    User,
)
from app.schemas import (
    ConversationSummaryResponse,
    ConversationUserResponse,
    MessageCreate,
    MessageEditRequest,
    MessageReactionRequest,
    MessageResponse,
)
from app.services.auth_service import authenticated_user
from app.services.chat_presenter import _build_message_response
from app.services.conversations import mark_messages_read
from app.services.conversations import member_or_403 as _member_or_403
from app.services.realtime_events import (
    publish_committed_events,
    queue_event,
    queue_notification,
)
from app.services.social_presenter import _user_avatar_url
from app.services.storage_service import photo_storage
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/social", tags=["social"])


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
async def conversations_list(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
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
                .order_by(ConversationMessage.created_at.desc())
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


@router.get("/conversations/{conversation_id}")
async def conversation_details(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
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
        (ws_manager.get_last_seen(other_user.id) or other_user.last_seen_at)
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


@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[MessageResponse]
)
async def messages(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[MessageResponse]:
    await _member_or_403(session, conversation_id, user.id)
    rows = (
        (
            await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at)
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


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
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
    elif msg_type == "location" and (
        payload.location_lat is None or payload.location_lng is None
    ):
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

    members = (
        (
            await session.execute(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id != user.id,
                )
            )
        )
        .scalars()
        .all()
    )

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


@router.post("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> dict:
    count = await mark_messages_read(session, conversation_id, user.id)
    await session.commit()
    await publish_committed_events(session)
    return {"status": "ok", "marked_count": count}


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
    reaction = payload.reaction
    user_id_str = str(user.id)
    users_list = list(reactions.get(reaction, []))

    if user_id_str in users_list:
        users_list.remove(user_id_str)
        if not users_list:
            reactions.pop(reaction, None)
        else:
            reactions[reaction] = users_list
    else:
        users_list.append(user_id_str)
        reactions[reaction] = users_list

    message.reactions = reactions
    await session.commit()

    members = (
        (
            await session.execute(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
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


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}",
    response_model=MessageResponse,
)
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
    members = (
        (
            await session.execute(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
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

    members = (
        (
            await session.execute(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
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
