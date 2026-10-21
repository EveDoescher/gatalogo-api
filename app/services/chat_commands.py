from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    HTTPException,
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import (
    AppNotification,
    ConversationMember,
    ConversationMessage,
    User,
)
from app.schemas import (
    MessageCreate,
    MessageEditRequest,
    MessageReactionRequest,
    MessageResponse,
)
from app.services.chat_presenter import _build_message_response
from app.services.conversations import mark_messages_read
from app.services.conversations import member_or_403 as _member_or_403
from app.services.realtime_events import (
    publish_committed_events,
    queue_event,
    queue_notification,
)
from app.services.storage_service import photo_storage


async def send_message(
    *, conversation_id: UUID, payload: MessageCreate, user: User, session: AsyncSession
) -> MessageResponse:
    await _member_or_403(session, conversation_id, user.id)
    body = (payload.body or "").strip()
    msg_type = payload.type or "text"
    if msg_type == "text" and (not body):
        raise HTTPException(422, "Escreva uma mensagem.")
    elif msg_type == "photo" and (not payload.media_url):
        raise HTTPException(422, "Foto não informada.")
    elif msg_type == "location" and (
        payload.location_lat is None or payload.location_lng is None
    ):
        raise HTTPException(422, "Coordenadas de localização não informadas.")
    elif msg_type == "cat_card" and (not payload.cat_data):
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


async def upload_chat_photo(
    *, conversation_id: UUID, file: UploadFile, user: User, session: AsyncSession
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
        conversation_id=conversation_id, message_id=msg_id, data=data, suffix=suffix
    )
    media_url = f"/social/conversations/{conversation_id}/photo/{msg_id}{suffix}"
    return {"media_url": media_url, "photo_key": key}


async def mark_conversation_read(
    *, conversation_id: UUID, user: User, session: AsyncSession
) -> dict:
    count = await mark_messages_read(session, conversation_id, user.id)
    await session.commit()
    await publish_committed_events(session)
    return {"status": "ok", "marked_count": count}


async def toggle_message_reaction(
    *,
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageReactionRequest,
    user: User,
    session: AsyncSession,
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


async def edit_message(
    *,
    conversation_id: UUID,
    message_id: UUID,
    payload: MessageEditRequest,
    user: User,
    session: AsyncSession,
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


async def delete_message(
    *, conversation_id: UUID, message_id: UUID, user: User, session: AsyncSession
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
