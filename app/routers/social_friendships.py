from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.dependencies import current_user
from app.persistence_models import (
    AppNotification,
    FriendInvite,
    Friendship,
    ProductState,
    User,
)
from app.schemas import (
    FriendInviteCreate,
    FriendInviteResponse,
    PublicUserResponse,
)
from app.services.conversations import friend_ids as _friend_ids
from app.services.realtime_events import (
    publish_committed_events,
    queue_notification,
)
from app.services.social_presenter import _public_user_response

router = APIRouter(prefix="/social", tags=["social"])


@router.post(
    "/invites", response_model=FriendInviteResponse, status_code=status.HTTP_201_CREATED
)
async def send_invite(
    payload: FriendInviteCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> FriendInviteResponse:
    sender = _public_user_response(user)
    recipient = (
        await session.execute(
            select(User).where(
                User.username == payload.username.lower(), User.status == "active"
            )
        )
    ).scalar_one_or_none()
    if recipient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado."
        )
    if recipient.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Você não pode adicionar a si mesma.",
        )
    if recipient.id in await _friend_ids(session, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Vocês já são amigos."
        )
    reverse = (
        await session.execute(
            select(FriendInvite).where(
                FriendInvite.sender_id == recipient.id,
                FriendInvite.recipient_id == user.id,
                FriendInvite.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if reverse is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Você já recebeu um convite desta pessoa. Aceite ou recuse-o primeiro.",
        )
    invite = (
        await session.execute(
            select(FriendInvite).where(
                FriendInvite.sender_id == user.id,
                FriendInvite.recipient_id == recipient.id,
            )
        )
    ).scalar_one_or_none()
    notify_invite = invite is None or invite.status != "pending"
    if invite is None:
        invite = FriendInvite(
            sender_id=user.id, recipient_id=recipient.id, status="pending"
        )
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
    return FriendInviteResponse(
        id=invite.id,
        sender=sender,
        recipient=_public_user_response(recipient),
        status=invite.status,
        created_at=invite.created_at,
    )


@router.get("/invites", response_model=list[FriendInviteResponse])
async def list_invites(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[FriendInviteResponse]:
    invites = (
        (
            await session.execute(
                select(FriendInvite)
                .where(
                    or_(
                        FriendInvite.sender_id == user.id,
                        FriendInvite.recipient_id == user.id,
                    )
                )
                .order_by(FriendInvite.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    result: list[FriendInviteResponse] = []
    for invite in invites:
        sender = await session.get(User, invite.sender_id)
        recipient = await session.get(User, invite.recipient_id)
        if sender and recipient and sender.username and recipient.username:
            s_state = await session.get(ProductState, sender.id)
            r_state = await session.get(ProductState, recipient.id)
            result.append(
                FriendInviteResponse(
                    id=invite.id,
                    sender=_public_user_response(
                        sender, s_state.preferences if s_state else None
                    ),
                    recipient=_public_user_response(
                        recipient, r_state.preferences if r_state else None
                    ),
                    status=invite.status,
                    created_at=invite.created_at,
                )
            )
    return result


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_invite(
    invite_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> None:
    invite = (
        await session.execute(
            select(FriendInvite).where(
                FriendInvite.id == invite_id,
                FriendInvite.sender_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Convite não encontrado."
        )
    if invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apenas convites pendentes podem ser cancelados.",
        )
    await session.delete(invite)
    await session.commit()


@router.post("/invites/{invite_id}/{decision}", response_model=FriendInviteResponse)
async def respond_invite(
    invite_id: UUID,
    decision: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> FriendInviteResponse:
    if decision not in {"accept", "reject"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Resposta inválida.",
        )
    invite = (
        await session.execute(
            select(FriendInvite)
            .where(FriendInvite.id == invite_id, FriendInvite.recipient_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None or invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Convite pendente não encontrado.",
        )
    invite.status = "accepted" if decision == "accept" else "rejected"
    invite.responded_at = datetime.now(UTC)
    if decision == "accept":
        session.add_all(
            (
                Friendship(user_id=invite.sender_id, friend_id=invite.recipient_id),
                Friendship(user_id=invite.recipient_id, friend_id=invite.sender_id),
            )
        )
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
    s_state = await session.get(ProductState, sender.id) if sender else None
    u_state = await session.get(ProductState, user.id)
    return FriendInviteResponse(
        id=invite.id,
        sender=_public_user_response(sender, s_state.preferences if s_state else None)
        if sender
        else _public_user_response(user),
        recipient=_public_user_response(user, u_state.preferences if u_state else None),
        status=invite.status,
        created_at=invite.created_at,
    )


@router.get("/friends", response_model=list[PublicUserResponse])
async def list_friends(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[PublicUserResponse]:
    friends = (
        (
            await session.execute(
                select(User)
                .join(Friendship, Friendship.friend_id == User.id)
                .where(Friendship.user_id == user.id)
                .order_by(User.username)
            )
        )
        .scalars()
        .all()
    )
    res = []
    for friend in friends:
        if friend.username:
            f_state = await session.get(ProductState, friend.id)
            res.append(
                _public_user_response(friend, f_state.preferences if f_state else None)
            )
    return res


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(
    friend_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> None:
    rows = (
        (
            await session.execute(
                select(Friendship).where(
                    or_(
                        (Friendship.user_id == user.id)
                        & (Friendship.friend_id == friend_id),
                        (Friendship.user_id == friend_id)
                        & (Friendship.friend_id == user.id),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await session.delete(row)
    await session.commit()
