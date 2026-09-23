"""Tests for the social graph and community discovery."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.persistence_models import FriendInvite, Friendship, ProductState, RemoteCat, User
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

    profile = await get_public_profile("target_hero", user=viewer, session=session)
    assert profile.username == "target_hero"
    assert profile.bio == "Amante dos bichanos"
    assert profile.xp == 450
    assert profile.level == 3
    assert profile.level_title == "Observador"
    assert profile.streak == 5
    assert profile.cats_count == 1
    assert len(profile.public_cats) == 1
    assert profile.public_cats[0]["name"] == "Mimi"
    assert len(profile.achievements) == 1
    assert profile.achievements[0]["id"] == "first"
    assert profile.friendship_status == "none"


@pytest.mark.asyncio
async def test_weekly_leaderboard():
    alice = User(id=uuid4(), username="alice", status="active")
    bob = User(id=uuid4(), username="bob", status="active")

    session = AsyncMock()

    # Friends query
    async def mock_execute(stmt):
        mock_res = MagicMock()
        stmt_str = str(stmt)
        if "friendships.friend_id" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [bob.id]
        elif "users.id IN" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [alice, bob]
        return mock_res

    session.execute.side_effect = mock_execute

    state_alice = ProductState(user_id=alice.id, preferences={"gamification": {"xp": 600, "streak": 7}})
    state_bob = ProductState(user_id=bob.id, preferences={"gamification": {"xp": 200, "streak": 2}})

    async def mock_get(model, pk):
        if pk == alice.id:
            return state_alice
        if pk == bob.id:
            return state_bob
        return None

    session.get.side_effect = mock_get

    lb = await weekly_leaderboard(user=alice, session=session)
    assert len(lb) == 2
    assert lb[0].username == "alice"
    assert lb[0].rank == 1
    assert lb[0].is_self is True
    assert lb[1].username == "bob"
    assert lb[1].rank == 2
    assert lb[1].is_self is False


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
