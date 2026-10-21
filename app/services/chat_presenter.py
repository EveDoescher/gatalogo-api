from __future__ import annotations

from uuid import UUID

from app.persistence_models import (
    ConversationMessage,
)
from app.schemas import (
    MessageResponse,
)


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
            "body": "Mensagem apagada"
            if reply_to_row.is_deleted
            else reply_to_row.body,
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
