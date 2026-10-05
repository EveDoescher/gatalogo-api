from __future__ import annotations

from typing import Literal

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
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
    RemoteCat,
    User,
)
from app.routers.auth import current_user
from app.routers.product import (
    ACHIEVEMENTS,
    ACHIEVEMENT_XP,
    calculate_level,
)
from app.schemas import (
    ConversationSummaryResponse,
    ConversationUserResponse,
    DeviceTokenRequest,
    FeedItemResponse,
    FriendInviteCreate,
    FriendInviteResponse,
    LeaderboardEntry,
    MessageCreate,
    MessageEditRequest,
    MessageReactionRequest,
    MessageResponse,
    PublicProfileResponse,
    PublicUserResponse,
    UserSearchResult,
    UserSuggestion,
)
from app.services.realtime_events import (
    publish_committed_events,
    queue_event,
    queue_notification,
)
from app.services.storage_service import photo_storage
from app.services import progression as progress
from app.services.audience import permits, can_read_post
from app.persistence_models import MissingReport, HabitatWallet, ProgressEvent
import hashlib
import json
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/social", tags=["social"])


def _user_avatar_url(user_id: UUID, preferences: dict | None) -> str | None:
    avatar_url = (preferences or {}).get("avatar_url") if preferences else None
    if not avatar_url:
        return None
    if avatar_url in ('/app/account/avatar/preset/bapinho', '/app/account/avatar/preset/bapinha'):
        return avatar_url
    try:
        path = photo_storage.absolute_path(photo_storage.profile_key_for(user_id, ".jpg"))
        if path.parent.is_dir() and any(path.parent.glob("avatar*")):
            return avatar_url
    except Exception:
        pass
    return None


def _public_user_response(user: User, preferences: dict | None = None) -> PublicUserResponse:
    if not user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Escolha um nome de usuário para usar recursos sociais.")
    avatar_url = _user_avatar_url(user.id, preferences) if preferences is not None else None
    bio = (preferences or {}).get("bio") if preferences is not None else None
    return PublicUserResponse(id=user.id, username=user.username, avatar_url=avatar_url, bio=bio)


async def _friend_ids(session: AsyncSession, user_id: UUID) -> set[UUID]:
    rows = await session.execute(select(Friendship.friend_id).where(Friendship.user_id == user_id))
    return set(rows.scalars().all())


async def _mutual_friends_count(session: AsyncSession, user_id: UUID, other_id: UUID) -> int:
    my_friends = await _friend_ids(session, user_id)
    their_friends = await _friend_ids(session, other_id)
    return len(my_friends.intersection(their_friends))


async def _get_friendship_status(session: AsyncSession, current_user_id: UUID, target_id: UUID) -> str:
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


@router.get("/users/search", response_model=list[UserSearchResult])
async def search_users(
    q: str = Query(default="", max_length=50),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[UserSearchResult]:
    query_clean = q.strip().lower().replace("@", "")
    if not query_clean:
        return []

    users = (
        await session.execute(
            select(User)
            .where(
                User.username.ilike(f"%{query_clean}%"),
                User.status == "active",
                User.id != user.id,
            )
            .limit(20)
        )
    ).scalars().all()

    results: list[UserSearchResult] = []
    my_friends = await _friend_ids(session, user.id)

    for u in users:
        if not u.username:
            continue
        pstate = await session.get(ProductState, u.id)
        prefs = pstate.preferences if pstate else {}
        their_friends = await _friend_ids(session, u.id)
        mutual_count = len(my_friends.intersection(their_friends))
        f_status = await _get_friendship_status(session, user.id, u.id)
        results.append(
            UserSearchResult(
                id=u.id,
                username=u.username,
                avatar_url=_user_avatar_url(u.id, prefs),
                bio=prefs.get("bio", ""),
                mutual_friends_count=mutual_count,
                friendship_status=f_status,
            )
        )
    return results


@router.get("/suggestions", response_model=list[UserSuggestion])
async def user_suggestions(
    nearby: bool = False,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[UserSuggestion]:
    my_friends = await _friend_ids(session, user.id)
    excluded_ids = set(my_friends)
    excluded_ids.add(user.id)
    excluded_ids.add(OFFICIAL_USER_ID)

    invites = (
        await session.execute(
            select(FriendInvite).where(
                or_(FriendInvite.sender_id == user.id, FriendInvite.recipient_id == user.id),
                FriendInvite.status == "pending",
            )
        )
    ).scalars().all()
    for inv in invites:
        excluded_ids.add(inv.sender_id)
        excluded_ids.add(inv.recipient_id)

    fof_counts: dict[UUID, int] = {}
    for f_id in my_friends:
        f_friends = await _friend_ids(session, f_id)
        for fof in f_friends:
            if fof not in excluded_ids:
                fof_counts[fof] = fof_counts.get(fof, 0) + 1

    suggestions: list[UserSuggestion] = []
    sorted_fof = sorted(fof_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for target_id, mutual_count in sorted_fof:
        target_user = await session.get(User, target_id)
        if target_user and target_user.status == "active" and target_user.username:
            pstate = await session.get(ProductState, target_id)
            prefs = pstate.preferences if pstate else {}
            suggestions.append(
                UserSuggestion(
                    id=target_user.id,
                    username=target_user.username,
                    avatar_url=_user_avatar_url(target_user.id, prefs),
                    bio=prefs.get("bio", ""),
                    mutual_friends_count=mutual_count,
                    reason=f"{mutual_count} amigo(s) em comum",
                )
            )
            excluded_ids.add(target_id)

    remaining_slots = 10 - len(suggestions)
    if remaining_slots > 0:
        candidates = (
            await session.execute(
                select(User)
                .where(
                    User.status == "active",
                    User.id.not_in(excluded_ids),
                    User.username.is_not(None),
                )
                .order_by(User.last_seen_at.desc().nulls_last(), User.created_at.desc())
                .limit(remaining_slots)
            )
        ).scalars().all()

        for cand in candidates:
            if cand.username:
                pstate = await session.get(ProductState, cand.id)
                prefs = pstate.preferences if pstate else {}
                their_friends = await _friend_ids(session, cand.id)
                m_count = len(my_friends.intersection(their_friends))
                reason = "Explorador na sua região" if nearby else "Explorador ativo da comunidade"
                suggestions.append(
                    UserSuggestion(
                        id=cand.id,
                        username=cand.username,
                        avatar_url=_user_avatar_url(cand.id, prefs),
                        bio=prefs.get("bio", ""),
                        mutual_friends_count=m_count,
                        reason=reason,
                    )
                )

    return suggestions


@router.get("/profiles/{username}", response_model=PublicProfileResponse)
async def get_public_profile(
    username: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
    view_as_friend: bool = False,
) -> PublicProfileResponse:
    target_user = (
        await session.execute(
            select(User).where(User.username == username.lower(), User.status == "active")
        )
    ).scalar_one_or_none()
    if target_user is None or not target_user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perfil não encontrado.")

    pstate = await session.get(ProductState, target_user.id)
    prefs = dict(pstate.preferences or {}) if pstate else {}
    gamif = prefs.get("gamification", {})
    achievements_dict = dict(pstate.achievements or {}) if pstate else {}

    xp = gamif.get("xp", 0)
    level, level_title, _, _ = progress.level_for(xp, gamif.get("highest_level", 1))
    streak = gamif.get("streak", 0)
    longest_streak = gamif.get("longest_streak", streak)

    cats = (
        await session.execute(
            select(RemoteCat).where(
                RemoteCat.user_id == target_user.id,
                RemoteCat.deleted_at.is_(None),
            ).order_by(RemoteCat.created_at.desc())
        )
    ).scalars().all()
    groups = {}
    for cat in cats:
        payload = cat.payload or {}
        if payload.get("identity_state") == "pending":
            continue
        key = str(payload.get("individual_id") or cat.client_id)
        if key not in groups or str(cat.client_id) == key:
            groups[key] = cat
    cats = list(groups.values())
    cats_count = len(cats)

    public_cats = []
    gallery_visible = await permits(session, user.id, target_user.id, "gallery", as_friend=view_as_friend and user.id == target_user.id)
    if gallery_visible:
        for c in cats[:20]:
            p = c.payload or {}
            public_cats.append({
                "id": str(c.id),
                "name": p.get("name") or "Gato sem nome",
                "primary_color": p.get("primary_color") or "indefinida",
                "coat_type": p.get("coat_type") or "curto",
                "captured_at": p.get("captured_at"),
                "is_owned": p.get("is_owned", False),
                "photo_url": f"/social/gallery/{c.id}/photo" if c.id else None,
            })

    unlocked_achievements = []
    medals_visible = await permits(session, user.id, target_user.id, "medals", as_friend=view_as_friend and user.id == target_user.id)
    definitions = {**ACHIEVEMENTS, **{c[0]: (c[1], c[2], c[3]) for c in progress.CHALLENGES}}
    from app.services import milestones
    medal_rules = {c[0]: c for c in milestones.ACHIEVEMENTS}
    highlights = prefs.get("highlighted_medals", [])
    receipts = {r["id"]: r for r in milestones.project(await progress.events_for(session, target_user.id), {**prefs, "preserved_achievements": achievements_dict})["achievements"]} if medals_visible and highlights else {}
    for ach_id, unlocked_at in (achievements_dict.items() if medals_visible else []):
        if ach_id in definitions and (highlights is None or ach_id in highlights):
            title, desc, _ = definitions[ach_id]
            unlocked_achievements.append({
                "id": ach_id,
                "title": title,
                "description": desc,
                "unlocked_at": unlocked_at,
                "xp_reward": medal_rules[ach_id][5] if ach_id in medal_rules else 0,
                "coin_reward": medal_rules[ach_id][6] if ach_id in medal_rules else 0,
                "ranking_points": medal_rules[ach_id][7] if ach_id in medal_rules else 0,
                "xp_received": receipts.get(ach_id, {}).get("xp_received"),
                "coin_received": receipts.get(ach_id, {}).get("coin_received"),
                "legacy_reward_unknown": receipts.get(ach_id, {}).get("legacy_reward_unknown", True),
                "type": "achievement", "completed": True, "unlocked": True,
            })

    f_status = await _get_friendship_status(session, user.id, target_user.id)
    mutual_count = await _mutual_friends_count(session, user.id, target_user.id) if user.id != target_user.id else 0

    return PublicProfileResponse(
        id=target_user.id,
        username=target_user.username,
        bio=prefs.get("bio", ""),
        avatar_url=_user_avatar_url(target_user.id, prefs),
        level=level,
        level_title=level_title,
        xp=xp,
        streak=streak,
        longest_streak=longest_streak,
        cats_count=cats_count if gallery_visible else 0,
        achievements=unlocked_achievements,
        friendship_status=f_status,
        mutual_friends_count=mutual_count,
        public_cats=public_cats,
        habitat_preview=await _habitat_preview(session, user.id, target_user.id, view_as_friend),
        audiences={k: prefs.get(f"{k}_audience", "friends") for k in ("gallery", "medals", "habitat")},
    )


@router.get("/leaderboard/weekly", response_model=list[LeaderboardEntry])
async def weekly_leaderboard(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
    criterion: Literal["cats", "missions", "milestones"] = "cats",
    period: Literal["weekly", "all"] = "weekly",
) -> list[LeaderboardEntry]:
    friend_ids = await _friend_ids(session, user.id)
    all_user_ids = list(friend_ids)
    all_user_ids.append(user.id)

    users = (
        await session.execute(
            select(User).where(User.id.in_(all_user_ids), User.status == "active")
        )
    ).scalars().all()

    from app.services import milestones
    from app.persistence_models import ProgressEvent
    states = {row.user_id: row for row in (await session.scalars(select(ProductState).where(ProductState.user_id.in_(all_user_ids)))).all()}
    grouped = {uid: [] for uid in all_user_ids}
    for event in (await session.scalars(select(ProgressEvent).where(ProgressEvent.user_id.in_(all_user_ids)))).all():
        grouped[event.user_id].append(event)
    clock = milestones.calendar()
    effective_period = "all" if criterion == "milestones" else "weekly" if criterion == "missions" else period
    entries = []
    for u in users:
        if not u.username:
            continue
        state = states.get(u.id)
        prefs = dict(state.preferences or {}) if state else {}
        prefs["preserved_achievements"] = dict(state.achievements or {}) if state else {}
        scores = milestones.scores(grouped[u.id], prefs)
        key = "cats_all" if criterion == "cats" and effective_period == "all" else "cats_weekly" if criterion == "cats" else criterion
        gamif = prefs.get("gamification", {})
        level, title, _, _ = progress.level_for(gamif.get("xp", 0), gamif.get("highest_level", 1))
        entries.append(dict(user_id=u.id, username=u.username, avatar_url=_user_avatar_url(u.id, prefs),
            xp=gamif.get("xp", 0), level=level, level_title=title, streak=0, is_self=u.id == user.id,
            score=scores[key], criterion=criterion, period=effective_period,
            server_now=clock["server_now"], period_start=clock["week"] if effective_period == "weekly" else None,
            reset_at=clock["weekly_reset_at"] if effective_period == "weekly" else None))
    entries.sort(key=lambda row: (-row["score"], str(row["user_id"])))
    previous_score, position = None, 0
    result = []
    for index, entry in enumerate(entries, 1):
        if entry["score"] != previous_score:
            position = index
        previous_score = entry["score"]
        result.append(LeaderboardEntry(rank=position, **entry))
    return result



@router.get("/users/{username}", response_model=PublicUserResponse)
async def find_user(username: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> PublicUserResponse:
    target = (await session.execute(select(User).where(User.username == username.lower(), User.status == "active"))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    pstate = await session.get(ProductState, target.id)
    return _public_user_response(target, pstate.preferences if pstate else None)


@router.post("/invites", response_model=FriendInviteResponse, status_code=status.HTTP_201_CREATED)
async def send_invite(payload: FriendInviteCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> FriendInviteResponse:
    sender = _public_user_response(user)
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
    return FriendInviteResponse(id=invite.id, sender=sender, recipient=_public_user_response(recipient), status=invite.status, created_at=invite.created_at)


@router.get("/invites", response_model=list[FriendInviteResponse])
async def list_invites(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[FriendInviteResponse]:
    invites = (await session.execute(select(FriendInvite).where(or_(FriendInvite.sender_id == user.id, FriendInvite.recipient_id == user.id)).order_by(FriendInvite.created_at.desc()))).scalars().all()
    result: list[FriendInviteResponse] = []
    for invite in invites:
        sender = await session.get(User, invite.sender_id)
        recipient = await session.get(User, invite.recipient_id)
        if sender and recipient and sender.username and recipient.username:
            s_state = await session.get(ProductState, sender.id)
            r_state = await session.get(ProductState, recipient.id)
            result.append(FriendInviteResponse(
                id=invite.id,
                sender=_public_user_response(sender, s_state.preferences if s_state else None),
                recipient=_public_user_response(recipient, r_state.preferences if r_state else None),
                status=invite.status,
                created_at=invite.created_at,
            ))
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convite não encontrado.")
    if invite.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas convites pendentes podem ser cancelados.")
    await session.delete(invite)
    await session.commit()


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
    s_state = await session.get(ProductState, sender.id) if sender else None
    u_state = await session.get(ProductState, user.id)
    return FriendInviteResponse(
        id=invite.id,
        sender=_public_user_response(sender, s_state.preferences if s_state else None) if sender else _public_user_response(user),
        recipient=_public_user_response(user, u_state.preferences if u_state else None),
        status=invite.status,
        created_at=invite.created_at,
    )


@router.get("/friends", response_model=list[PublicUserResponse])
async def list_friends(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> list[PublicUserResponse]:
    friends = (await session.execute(select(User).join(Friendship, Friendship.friend_id == User.id).where(Friendship.user_id == user.id).order_by(User.username))).scalars().all()
    res = []
    for friend in friends:
        if friend.username:
            f_state = await session.get(ProductState, friend.id)
            res.append(_public_user_response(friend, f_state.preferences if f_state else None))
    return res


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(friend_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> None:
    rows = (await session.execute(select(Friendship).where(or_((Friendship.user_id == user.id) & (Friendship.friend_id == friend_id), (Friendship.user_id == friend_id) & (Friendship.friend_id == user.id))))).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.commit()


class CatPostCreate(BaseModel):
    cat_name: str = Field(default="", max_length=100)
    request_id: UUID | None = None
    note: str = Field(default="", max_length=2000)
    location_name: str | None = None
    photo_base64: str | None = None
    photo_url: str | None = None
    is_public: bool = True


class CommentCreate(BaseModel):
    text: str


OFFICIAL_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


async def _habitat_preview(session, viewer_id, owner_id, as_friend=False):
    if not await permits(session, viewer_id, owner_id, "habitat", as_friend=as_friend and viewer_id == owner_id):
        return None
    wallet = await session.get(HabitatWallet, owner_id)
    if wallet is None:
        return {"cats_count": 0, "expansion_level": 0}
    return {"cats_count": len(wallet.selected_cat_ids or []), "expansion_level": wallet.expansion_level}


@router.get("/reports")
async def community_reports(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    rows = (await session.execute(select(MissingReport, RemoteCat, User.username)
        .join(RemoteCat, RemoteCat.id == MissingReport.cat_id)
        .join(User, User.id == MissingReport.user_id)
        .where(MissingReport.status == "active", RemoteCat.deleted_at.is_(None))
        .order_by(MissingReport.created_at.desc()).limit(50))).all()
    return [{"id": str(report.id), "name": cat.payload.get("name") or "Gato desaparecido",
             "author": username or "Tutor", "is_owner": report.user_id == user.id,
             "status": report.status, "photo_url": f"/app/reports/{report.id}/photo"}
            for report, cat, username in rows]


@router.get("/reports/{report_id}")
async def community_report(report_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    report = await session.get(MissingReport, report_id)
    cat = await session.get(RemoteCat, report.cat_id) if report else None
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(404, "Busca não disponível.")
    owner = await session.get(User, report.user_id)
    from app.services.missing_posters import public_poster_details
    details = report.poster_details or {}
    return {**public_poster_details(report, cat), "id": str(report.id), "status": report.status, "is_owner": user.id == report.user_id,
        "name": cat.payload.get("name") or "Gato desaparecido", "author": owner.username or "Tutor",
        "description": details.get("description"),
        "photo_url": f"/app/reports/{report.id}/photo" if report.status == "active" else None,
        "message": "As pistas são avaliadas pelo tutor. As localizações dos colaboradores são privadas."}


@router.get("/gallery/{cat_id}/photo")
async def gallery_photo(cat_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    cat = await session.get(RemoteCat, cat_id)
    if cat is None or cat.deleted_at is not None or not cat.photo_key or not await permits(session, user.id, cat.user_id, "gallery"):
        raise HTTPException(404, "Foto não disponível.")
    from io import BytesIO
    from PIL import Image, ImageOps
    with Image.open(photo_storage.open(cat.photo_key)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1200, 1200))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/reports/{report_id}/repost", response_model=FeedItemResponse)
async def repost_report(report_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    return await _share_report(report_id, user, session, owner_publication=False)


@router.post("/reports/{report_id}/publish", response_model=FeedItemResponse)
async def publish_report(report_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    return await _share_report(report_id, user, session, owner_publication=True)


async def _share_report(report_id, user, session, *, owner_publication):
    await progress.account(session, user.id)
    report = await session.get(MissingReport, report_id, with_for_update=True)
    if report is None or report.status != "active":
        raise HTTPException(409, "Este cartaz não está mais ativo.")
    if owner_publication and report.user_id != user.id:
        raise HTTPException(404, "Alerta não encontrado.")
    if not owner_publication and report.user_id == user.id:
        raise HTTPException(422, "Use a publicação do seu cartaz para divulgar sua busca.")
    previous = await session.scalar(select(ProgressEvent).where(ProgressEvent.user_id == user.id,
        ProgressEvent.event_key == f"repost:{report.id}"))
    if previous:
        item = await session.get(FeedItem, UUID(previous.record_id))
    else:
        cat = await session.get(RemoteCat, report.cat_id)
        owner = await session.get(User, report.user_id)
        item = FeedItem(user_id=user.id, type="poster_repost", payload={
            "report_id": str(report.id), "original_author": owner.username or "Tutor",
            "cat_name": cat.payload.get("name") or "Gato desaparecido",
            "note": "Ajude esta busca. Uma pista útil pode fazer a diferença.",
            "photo_url": f"/app/reports/{report.id}/photo", "is_public": True,
            "report_status": "active", "likes": [], "comments": []})
        session.add(item)
        await session.flush()
        event = await progress.grant(session, user.id, f"repost:{report.id}", "post" if owner_publication else "repost",
            record_id=str(item.id), evidence={"report_id": str(report.id), "original_author_id": str(report.user_id)})
        await progress.finish(session, event)
    await session.commit()
    await publish_committed_events(session)
    await session.refresh(item)
    return FeedItemResponse(id=item.id, username=user.username or "Amigo", type=item.type,
                            payload=item.payload, created_at=item.created_at)


@router.get("/feed", response_model=list[FeedItemResponse])
async def feed(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: datetime | None = Query(default=None),
    mode: str = Query(default="all"),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> list[FeedItemResponse]:
    parts = mode.split(":", 1)
    mode = parts[0]
    content_type = parts[1] if len(parts) > 1 else "all"
    query = select(FeedItem, User.username).join(User, User.id == FeedItem.user_id).where(
        FeedItem.user_id != OFFICIAL_USER_ID
    )

    if content_type in {"cat_post", "achievement", "poster_repost"}:
        query = query.where(FeedItem.type == content_type)
    if mode == "friends":
        friend_ids = await _friend_ids(session, user.id)
        friend_ids.add(user.id)
        query = query.where(FeedItem.user_id.in_(friend_ids))
    else:
        friend_ids = await _friend_ids(session, user.id)
        friend_ids.add(user.id)
        query = query.where(
            or_(
                FeedItem.user_id.in_(friend_ids),
                FeedItem.type.in_(["cat_post", "achievement", "tip", "poster_repost"]),
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
        if not await can_read_post(session, item, user.id):
            continue
        if item.type == "poster_repost":
            report = await session.get(MissingReport, UUID(p["report_id"]))
            p["report_status"] = report.status if report else "closed"
            p["can_repost"] = report is not None and report.status == "active" and report.user_id != user.id
        likes = list(p.get("likes", []))
        p["likes_count"] = len(likes)
        p["has_liked"] = str(user.id) in likes
        p["comments"] = list(p.get("comments", []))
        pstate = await session.get(ProductState, item.user_id)
        p["author_avatar_url"] = _user_avatar_url(item.user_id, pstate.preferences if pstate else None)
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
    session: AsyncSession = Depends(get_database_session),
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
    session.add(ProgressEvent(user_id=user.id, event_key=f"media:{photo_id}", kind="media_upload",
        state="confirmed", xp=0, coins=0, evidence={"photo_url": photo_url}, result={}))
    await session.commit()
    return {"photo_url": photo_url, "photo_key": key}


@router.get("/posts/photo/{filename}")
async def get_post_photo(filename: str, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_database_session)) -> Response:
    if Path(filename).name != filename:
        raise HTTPException(404, "Foto não encontrada.")
    try:
        photo_id = UUID(Path(filename).stem)
    except ValueError:
        raise HTTPException(404, "Foto não encontrada.")
    url = f"/social/posts/photo/{filename}"
    upload = await session.scalar(select(ProgressEvent).where(ProgressEvent.user_id == user.id,
        ProgressEvent.event_key == f"media:{photo_id}"))
    posts = (await session.scalars(select(FeedItem).where(FeedItem.payload["photo_url"].as_string() == url))).all()
    allowed = upload is not None
    for post in posts:
        allowed = allowed or await can_read_post(session, post, user.id)
    if not allowed:
        raise HTTPException(404, "Foto não disponível.")
    files = list(photo_storage.absolute_path("posts").glob(f"{photo_id}-*{Path(filename).suffix}"))
    if not files:
        raise HTTPException(404, "Foto não encontrada.")
    photo_file = files[0]
    from io import BytesIO
    from PIL import Image, ImageOps
    with Image.open(photo_file) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1600, 1600))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/posts", response_model=FeedItemResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: CatPostCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> FeedItemResponse:
    await progress.account(session, user.id)
    if not user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Escolha um nome de usuário para postar no feed.")
    cat_name = payload.cat_name.strip()
    if not payload.note.strip() and not payload.photo_url and not payload.photo_base64:
        raise HTTPException(422, "Escreva algo ou adicione uma foto.")
    request_content = hashlib.sha256(json.dumps({
        "cat_name": cat_name, "note": payload.note.strip(),
        "location_name": payload.location_name, "photo_url": payload.photo_url,
        "photo_base64": payload.photo_base64, "is_public": payload.is_public,
    }, sort_keys=True).encode()).hexdigest()
    if payload.request_id:
        await session.scalar(select(User).where(User.id == user.id).with_for_update())
        previous = await session.scalar(select(FeedItem).where(FeedItem.user_id == user.id,
            FeedItem.payload["request_id"].as_string() == str(payload.request_id)))
        if previous is not None:
            if previous.payload.get("request_content") != request_content:
                raise HTTPException(409, "Esta tentativa já publicou outro conteúdo. Inicie uma nova publicação.")
            return FeedItemResponse(id=previous.id, username=user.username, type=previous.type,
                payload=previous.payload or {}, created_at=previous.created_at)

    photo_url = payload.photo_url
    if photo_url:
        owned_upload = await session.scalar(select(ProgressEvent).where(ProgressEvent.user_id == user.id,
            ProgressEvent.kind == "media_upload", ProgressEvent.evidence["photo_url"].as_string() == photo_url))
        if owned_upload is None:
            raise HTTPException(422, "Envie uma foto da sua conta para esta publicação.")
    if not photo_url and payload.photo_base64:
        try:
            import base64
            raw_bytes = base64.b64decode(payload.photo_base64, validate=True)
            if raw_bytes:
                pid = uuid4()
                photo_storage.save_post_photo(post_id=pid, data=raw_bytes, suffix=".jpg")
                photo_url = f"/social/posts/photo/{pid}.jpg"
        except Exception as error:
            raise HTTPException(422, "Não foi possível processar a foto. Envie-a novamente.") from error
    if not photo_url and not payload.note.strip():
        raise HTTPException(422, "Escreva algo ou adicione uma foto válida.")

    item_payload = {
        "cat_name": cat_name,
        "request_id": str(payload.request_id) if payload.request_id else None,
        "request_content": request_content,
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
    fingerprint = hashlib.sha256((payload.note.strip().casefold() + "|" + cat_name.casefold()).encode()).hexdigest()
    event = await progress.grant(session, user.id, f"post:{fingerprint}", "post", record_id=str(item.id),
        evidence={"eligible": len(payload.note.strip()) >= 8, "post_id": str(item.id)})
    if not event.result:
        await progress.finish(session, event)
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
    await progress.account(session, user.id)
    item = await session.get(FeedItem, item_id, with_for_update=True)
    if item is None or not await can_read_post(session, item, user.id):
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
    await progress.account(session, user.id)
    item = await session.get(FeedItem, item_id, with_for_update=True)
    if item is None or not await can_read_post(session, item, user.id):
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

    if item.user_id != user.id:
        fingerprint = hashlib.sha256(text.casefold().encode()).hexdigest()
        event = await progress.grant(session, user.id, f"comment:{fingerprint}", "comment", record_id=str(item.id),
            evidence={"eligible": len(text) >= 8, "post_id": str(item.id)})
        if not event.result:
            await progress.finish(session, event)

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
