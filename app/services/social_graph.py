from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import (
    FriendInvite,
)
from app.services.conversations import friend_ids as _friend_ids


async def _mutual_friends_count(
    session: AsyncSession, user_id: UUID, other_id: UUID
) -> int:
    my_friends = await _friend_ids(session, user_id)
    their_friends = await _friend_ids(session, other_id)
    return len(my_friends.intersection(their_friends))


async def _get_friendship_status(
    session: AsyncSession, current_user_id: UUID, target_id: UUID
) -> str:
    if current_user_id == target_id:
        return "self"
    friends = await _friend_ids(session, current_user_id)
    if target_id in friends:
        return "friends"
    pending_sent = (
        await session.execute(
            select(FriendInvite.id).where(
                FriendInvite.sender_id == current_user_id,
                FriendInvite.recipient_id == target_id,
                FriendInvite.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if pending_sent:
        return "pending_sent"
    pending_recv = (
        await session.execute(
            select(FriendInvite.id).where(
                FriendInvite.sender_id == target_id,
                FriendInvite.recipient_id == current_user_id,
                FriendInvite.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if pending_recv:
        return "pending_received"
    return "none"


OFFICIAL_USER_ID = UUID("00000000-0000-0000-0000-000000000000")
