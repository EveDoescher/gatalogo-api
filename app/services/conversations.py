"""Conversation access rules shared by social and product endpoints."""

from uuid import UUID
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
)
from app.services.realtime_events import queue_event


async def friend_ids(session: AsyncSession, user_id: UUID) -> set[UUID]:
    rows = await session.execute(
        select(Friendship.friend_id).where(Friendship.user_id == user_id)
    )
    return set(rows.scalars().all())


async def member_or_403(
    session: AsyncSession, conversation_id: UUID, user_id: UUID
) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada."
        )
    member = (
        await session.execute(
            select(ConversationMember.id).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não participa desta conversa.",
        )
    if conv.match_suggestion_id is None:
        others = (
            (
                await session.execute(
                    select(ConversationMember.user_id).where(
                        ConversationMember.conversation_id == conversation_id,
                        ConversationMember.user_id != user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not others or not set(others).issubset(await friend_ids(session, user_id)):
            raise HTTPException(403, "As conversas estão disponíveis entre amigos.")
    return conv


async def conversation_recipients(session, conversation_id, user_id):
    """Authorize access before disclosing recipients or sending any event."""
    await member_or_403(session, conversation_id, user_id)
    return list(
        (
            await session.scalars(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id != user_id,
                )
            )
        ).all()
    )


async def mark_messages_read(session, conversation_id, user_id):
    """Stage read receipts; the HTTP/WebSocket caller commits and publishes."""
    recipients = await conversation_recipients(session, conversation_id, user_id)
    now = datetime.now(UTC)
    messages = list(
        (
            await session.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.sender_id != user_id,
                    ConversationMessage.read_at.is_(None),
                )
            )
        ).all()
    )
    for message in messages:
        message.read_at = now
    for recipient in recipients:
        queue_event(
            session,
            user_id=recipient,
            event="messages_read",
            data={
                "conversation_id": str(conversation_id),
                "reader_id": str(user_id),
                "read_at": now.isoformat(),
            },
        )
    return len(messages)
