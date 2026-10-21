from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.dependencies import current_user
from app.persistence_models import (
    FriendInvite,
    ProductState,
    RemoteCat,
    User,
)
from app.schemas import (
    LeaderboardEntry,
    PublicProfileResponse,
    PublicUserResponse,
    UserSearchResult,
    UserSuggestion,
)
from app.services import progression as progress
from app.services.audience import permits
from app.services.conversations import friend_ids as _friend_ids
from app.services.milestones import LEGACY_ACHIEVEMENTS as ACHIEVEMENTS
from app.services.social_graph import (
    OFFICIAL_USER_ID,
    _get_friendship_status,
    _mutual_friends_count,
)
from app.services.social_presenter import (
    _habitat_preview,
    _public_user_response,
    _user_avatar_url,
)

router = APIRouter(prefix="/social", tags=["social"])


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
        (
            await session.execute(
                select(User)
                .where(
                    User.username.ilike(f"%{query_clean}%"),
                    User.status == "active",
                    User.id != user.id,
                )
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

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
        (
            await session.execute(
                select(FriendInvite).where(
                    or_(
                        FriendInvite.sender_id == user.id,
                        FriendInvite.recipient_id == user.id,
                    ),
                    FriendInvite.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
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
            (
                await session.execute(
                    select(User)
                    .where(
                        User.status == "active",
                        User.id.not_in(excluded_ids),
                        User.username.is_not(None),
                    )
                    .order_by(
                        User.last_seen_at.desc().nulls_last(), User.created_at.desc()
                    )
                    .limit(remaining_slots)
                )
            )
            .scalars()
            .all()
        )

        for cand in candidates:
            if cand.username:
                pstate = await session.get(ProductState, cand.id)
                prefs = pstate.preferences if pstate else {}
                their_friends = await _friend_ids(session, cand.id)
                m_count = len(my_friends.intersection(their_friends))
                reason = (
                    "Explorador na sua região"
                    if nearby
                    else "Explorador ativo da comunidade"
                )
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
            select(User).where(
                User.username == username.lower(), User.status == "active"
            )
        )
    ).scalar_one_or_none()
    if target_user is None or not target_user.username:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Perfil não encontrado."
        )

    pstate = await session.get(ProductState, target_user.id)
    prefs = dict(pstate.preferences or {}) if pstate else {}
    gamif = prefs.get("gamification", {})
    achievements_dict = dict(pstate.achievements or {}) if pstate else {}

    xp = gamif.get("xp", 0)
    level, level_title, _, _ = progress.level_for(xp, gamif.get("highest_level", 1))
    streak = gamif.get("streak", 0)
    longest_streak = gamif.get("longest_streak", streak)

    cats = (
        (
            await session.execute(
                select(RemoteCat)
                .where(
                    RemoteCat.user_id == target_user.id,
                    RemoteCat.deleted_at.is_(None),
                )
                .order_by(RemoteCat.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
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
    gallery_visible = await permits(
        session,
        user.id,
        target_user.id,
        "gallery",
        as_friend=view_as_friend and user.id == target_user.id,
    )
    if gallery_visible:
        for c in cats[:20]:
            p = c.payload or {}
            public_cats.append(
                {
                    "id": str(c.id),
                    "name": p.get("name") or "Gato sem nome",
                    "primary_color": p.get("primary_color") or "indefinida",
                    "coat_type": p.get("coat_type") or "curto",
                    "captured_at": p.get("captured_at"),
                    "is_owned": p.get("is_owned", False),
                    "photo_url": f"/social/gallery/{c.id}/photo" if c.id else None,
                }
            )

    unlocked_achievements = []
    medals_visible = await permits(
        session,
        user.id,
        target_user.id,
        "medals",
        as_friend=view_as_friend and user.id == target_user.id,
    )
    definitions = {
        **ACHIEVEMENTS,
        **{c[0]: (c[1], c[2], c[3]) for c in progress.CHALLENGES},
    }
    from app.services import milestones

    medal_rules = {c[0]: c for c in milestones.ACHIEVEMENTS}
    highlights = prefs.get("highlighted_medals", [])
    receipts = (
        {
            r["id"]: r
            for r in milestones.project(
                await progress.events_for(session, target_user.id),
                {**prefs, "preserved_achievements": achievements_dict},
            )["achievements"]
        }
        if medals_visible and highlights
        else {}
    )
    for ach_id, unlocked_at in achievements_dict.items() if medals_visible else []:
        if ach_id in definitions and (highlights is None or ach_id in highlights):
            title, desc, _ = definitions[ach_id]
            unlocked_achievements.append(
                {
                    "id": ach_id,
                    "title": title,
                    "description": desc,
                    "unlocked_at": unlocked_at,
                    "xp_reward": medal_rules[ach_id][5] if ach_id in medal_rules else 0,
                    "coin_reward": medal_rules[ach_id][6]
                    if ach_id in medal_rules
                    else 0,
                    "ranking_points": medal_rules[ach_id][7]
                    if ach_id in medal_rules
                    else 0,
                    "xp_received": receipts.get(ach_id, {}).get("xp_received"),
                    "coin_received": receipts.get(ach_id, {}).get("coin_received"),
                    "legacy_reward_unknown": receipts.get(ach_id, {}).get(
                        "legacy_reward_unknown", True
                    ),
                    "type": "achievement",
                    "completed": True,
                    "unlocked": True,
                }
            )

    f_status = await _get_friendship_status(session, user.id, target_user.id)
    mutual_count = (
        await _mutual_friends_count(session, user.id, target_user.id)
        if user.id != target_user.id
        else 0
    )

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
        habitat_preview=await _habitat_preview(
            session, user.id, target_user.id, view_as_friend
        ),
        audiences={
            k: prefs.get(f"{k}_audience", "friends")
            for k in ("gallery", "medals", "habitat")
        },
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
        (
            await session.execute(
                select(User).where(User.id.in_(all_user_ids), User.status == "active")
            )
        )
        .scalars()
        .all()
    )

    from app.persistence_models import ProgressEvent
    from app.services import milestones

    states = {
        row.user_id: row
        for row in (
            await session.scalars(
                select(ProductState).where(ProductState.user_id.in_(all_user_ids))
            )
        ).all()
    }
    grouped = {uid: [] for uid in all_user_ids}
    for event in (
        await session.scalars(
            select(ProgressEvent).where(ProgressEvent.user_id.in_(all_user_ids))
        )
    ).all():
        grouped[event.user_id].append(event)
    clock = milestones.calendar()
    effective_period = (
        "all"
        if criterion == "milestones"
        else "weekly"
        if criterion == "missions"
        else period
    )
    entries = []
    for u in users:
        if not u.username:
            continue
        state = states.get(u.id)
        prefs = dict(state.preferences or {}) if state else {}
        prefs["preserved_achievements"] = (
            dict(state.achievements or {}) if state else {}
        )
        scores = milestones.scores(grouped[u.id], prefs)
        key = (
            "cats_all"
            if criterion == "cats" and effective_period == "all"
            else "cats_weekly"
            if criterion == "cats"
            else criterion
        )
        gamif = prefs.get("gamification", {})
        level, title, _, _ = progress.level_for(
            gamif.get("xp", 0), gamif.get("highest_level", 1)
        )
        entries.append(
            dict(
                user_id=u.id,
                username=u.username,
                avatar_url=_user_avatar_url(u.id, prefs),
                xp=gamif.get("xp", 0),
                level=level,
                level_title=title,
                streak=0,
                is_self=u.id == user.id,
                score=scores[key],
                criterion=criterion,
                period=effective_period,
                server_now=clock["server_now"],
                period_start=clock["week"] if effective_period == "weekly" else None,
                reset_at=clock["weekly_reset_at"]
                if effective_period == "weekly"
                else None,
            )
        )
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
async def find_user(
    username: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> PublicUserResponse:
    target = (
        await session.execute(
            select(User).where(
                User.username == username.lower(), User.status == "active"
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado."
        )
    pstate = await session.get(ProductState, target.id)
    return _public_user_response(target, pstate.preferences if pstate else None)
