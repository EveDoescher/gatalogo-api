"""Tests for the social graph and community discovery."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.persistence_models import FriendInvite, ProductState, RemoteCat, User
from app.routers.social import (
    cancel_invite,
    get_public_profile,
    search_users,
    user_suggestions,
    weekly_leaderboard,
)


@pytest.mark.asyncio
async def test_search_users_and_friendship_status():
    current_u = User(id=uuid4(), username="alice", status="active")
    bob = User(id=uuid4(), username="bobcat", status="active")
    carol = User(id=uuid4(), username="bobby", status="active")

    session = AsyncMock()
    # Mock friend ids
    session.execute = AsyncMock()

    # Search query matches bob and carol
    async def mock_execute(stmt):
        mock_res = MagicMock()
        stmt_str = str(stmt)
        if "users.username" in stmt_str and "LIKE" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [bob, carol]
        elif "friendships.user_id" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [bob.id]  # alice is friend with bob
        elif "friend_invites" in stmt_str:
            mock_res.scalar_one_or_none.return_value = None
        else:
            mock_res.scalars.return_value.all.return_value = []
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    session.execute.side_effect = mock_execute
    session.get = AsyncMock(return_value=None)

    results = await search_users(q="bob", user=current_u, session=session)
    assert len(results) == 2
    bob_res = next(r for r in results if r.username == "bobcat")
    carol_res = next(r for r in results if r.username == "bobby")

    assert bob_res.friendship_status == "friends"
    assert carol_res.friendship_status == "none"


@pytest.mark.asyncio
async def test_cancel_sent_invite():
    alice = User(id=uuid4(), username="alice", status="active")
    bob = User(id=uuid4(), username="bob", status="active")
    invite_id = uuid4()
    invite = FriendInvite(id=invite_id, sender_id=alice.id, recipient_id=bob.id, status="pending")

    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = invite
    session.execute = AsyncMock(return_value=mock_res)
    session.delete = AsyncMock()
    session.commit = AsyncMock()

    await cancel_invite(invite_id, user=alice, session=session)
    session.delete.assert_awaited_once_with(invite)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_public_profile():
    viewer = User(id=uuid4(), username="viewer", status="active")
    target = User(id=uuid4(), username="target_hero", status="active")

    target_state = ProductState(
        user_id=target.id,
        preferences={"gamification": {"xp": 450, "streak": 5}, "bio": "Amante dos bichanos"},
        achievements={"first": "2026-09-10T12:00:00Z"},
    )

    cat1 = RemoteCat(
        id=uuid4(),
        user_id=target.id,
        client_id=uuid4(),
        payload={"name": "Mimi", "primary_color": "laranja", "coat_type": "curto", "is_owned": True},
        deleted_at=None,
    )

    session = AsyncMock()

    async def mock_execute(stmt):
        mock_res = MagicMock()
        stmt_str = str(stmt)
        if "users.username" in stmt_str:
            mock_res.scalar_one_or_none.return_value = target
        elif "cats" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [cat1]
        elif "friendships" in stmt_str:
            mock_res.scalars.return_value.all.return_value = []
        elif "friend_invites" in stmt_str:
            mock_res.scalar_one_or_none.return_value = None
        else:
            mock_res.scalar_one_or_none.return_value = None
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    session.execute.side_effect = mock_execute
    session.get = AsyncMock(return_value=target_state)

    session.scalar = AsyncMock(return_value=None)
    profile = await get_public_profile("target_hero", user=viewer, session=session)
    assert profile.username == "target_hero"
    assert profile.bio == "Amante dos bichanos"
    assert profile.xp == 450
    assert profile.level == 3
    assert profile.level_title == "Observador"
    assert profile.streak == 5
    assert profile.cats_count == 0
    assert profile.public_cats == []
    assert profile.achievements == []
    assert profile.habitat_preview is None
    assert profile.friendship_status == "none"


from test_progression import account

@pytest.mark.asyncio
async def test_weekly_leaderboard(account, monkeypatch):
    from app.services import progression as p
    from app.routers import social
    session, alice = account
    alice.username = "alice"
    bob = User(email="ranking@test.local", username="bob", status="active")
    session.add(bob)
    await session.flush()
    state, _ = await p.account(session,alice.id)
    state.preferences = {**state.preferences,"gamification":{"xp":99999}}
    monkeypatch.setattr(social,"_friend_ids",AsyncMock(return_value={bob.id}))
    await p.grant(session,bob.id,"rank-photo","discovery",record_id="bob-cat",evidence={"individual_id":"bob-cat"})
    lb = await weekly_leaderboard(user=alice,session=session)
    assert [row.username for row in lb] == ["bob","alice"]
    assert [row.score for row in lb] == [1,0]
    assert lb[1].is_self and lb[0].reset_at and lb[0].server_now
    missions = await weekly_leaderboard(user=alice,session=session,criterion="missions")
    assert [row.rank for row in missions] == [1,1]
    assert all(row.score == 0 for row in missions)
    medals = await weekly_leaderboard(user=alice,session=session,criterion="milestones")
    assert all(row.period == "all" and row.reset_at is None for row in medals)


@pytest.mark.asyncio
async def test_user_suggestions():
    alice = User(id=uuid4(), username="alice", status="active")
    suggested = User(id=uuid4(), username="cat_lover", status="active")

    session = AsyncMock()

    async def mock_execute(stmt):
        mock_res = MagicMock()
        stmt_str = str(stmt)
        if "friendships.friend_id" in stmt_str:
            mock_res.scalars.return_value.all.return_value = []
        elif "friend_invites" in stmt_str:
            mock_res.scalars.return_value.all.return_value = []
        elif "users.id NOT IN" in stmt_str or "users.status" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [suggested]
        return mock_res

    session.execute.side_effect = mock_execute
    session.get = AsyncMock(return_value=None)

    suggs = await user_suggestions(nearby=True, user=alice, session=session)
    assert len(suggs) == 1
    assert suggs[0].username == "cat_lover"
    assert "região" in suggs[0].reason

# Imported pytest fixtures are discovered through module globals.
__all__ = ['account']
