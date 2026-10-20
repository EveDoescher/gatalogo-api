"""Conversation access rules shared by social and product endpoints."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import Conversation, ConversationMember, Friendship


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
