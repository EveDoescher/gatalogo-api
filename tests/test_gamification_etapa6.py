"""Unit tests for Gamification (Etapa 6) - XP, Levels, Streaks, Missions and Achievements."""
from datetime import UTC, datetime, date
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers.product import (
    ACHIEVEMENTS,
    LEVELS,
    calculate_level,
    calculate_multiplier,
    _get_or_init_gamification,
    record_gamification_action,
    get_gamification,
    GamificationActionRequest,
)
from app.persistence_models import ProductState, User


def test_level_calculation():
    # 7 levels verification
    assert len(LEVELS) == 7
    lvl, title, min_x, target, reward = calculate_level(0)
    assert lvl == 1
    assert title == "Curioso"
    assert target == 100

    lvl, title, min_x, target, reward = calculate_level(150)
    assert lvl == 2
    assert title == "Explorador"

    lvl, title, min_x, target, reward = calculate_level(400)
    assert lvl == 3
    assert title == "Observador"

    lvl, title, min_x, target, reward = calculate_level(800)
    assert lvl == 4
    assert title == "Catalogador"

    lvl, title, min_x, target, reward = calculate_level(1500)
    assert lvl == 5
    assert title == "Guardião"

    lvl, title, min_x, target, reward = calculate_level(2500)
    assert lvl == 6
    assert title == "Protetor"

    lvl, title, min_x, target, reward = calculate_level(4000)
    assert lvl == 7
    assert title == "Lenda Felina"


def test_multiplier_progression():
    assert calculate_multiplier(0) == 1.0
    assert calculate_multiplier(1) == 1.0
    assert calculate_multiplier(2) == 1.1
    assert calculate_multiplier(3) == 1.2
    assert calculate_multiplier(5) == 1.3
    assert calculate_multiplier(7) == 1.5
    assert calculate_multiplier(30) == 1.5


def test_achievements_count_and_keys():
    # Must have 25+ achievements (we implemented 28)
    assert len(ACHIEVEMENTS) >= 25
    assert "first" in ACHIEVEMENTS
    assert "collector_5" in ACHIEVEMENTS
    assert "collector_10" in ACHIEVEMENTS
    assert "master_collector" in ACHIEVEMENTS
    assert "legend_collector" in ACHIEVEMENTS
    assert "black_cat" in ACHIEVEMENTS
    assert "orange_tabby" in ACHIEVEMENTS
    assert "calico" in ACHIEVEMENTS
    assert "siamese_pattern" in ACHIEVEMENTS
    assert "bicolor" in ACHIEVEMENTS
    assert "neighborhood_3" in ACHIEVEMENTS
    assert "city_wanderer" in ACHIEVEMENTS
    assert "night_owl" in ACHIEVEMENTS
    assert "early_bird" in ACHIEVEMENTS
    assert "days" in ACHIEVEMENTS
    assert "streak_3" in ACHIEVEMENTS
    assert "streak_7" in ACHIEVEMENTS
    assert "streak_30" in ACHIEVEMENTS
    assert "angles" in ACHIEVEMENTS
    assert "portrait" in ACHIEVEMENTS
    assert "angles_master" in ACHIEVEMENTS
    assert "helper" in ACHIEVEMENTS
    assert "guardian_shield" in ACHIEVEMENTS
    assert "poster_share" in ACHIEVEMENTS
    assert "social_heart" in ACHIEVEMENTS
    assert "commentator" in ACHIEVEMENTS
    assert "first_share" in ACHIEVEMENTS
    assert "owned_pet" in ACHIEVEMENTS


def test_gamification_init():
    state = ProductState(user_id=uuid4(), preferences={}, achievements={})
    gamif = _get_or_init_gamification(state)
    assert gamif["xp"] == 0
    assert gamif["streak"] == 0
    assert gamif["freeze_shields"] == 1
    assert len(gamif["daily_missions"]) == 3
    assert gamif["weekly_mission"]["target"] == 3


@pytest.mark.asyncio
async def test_record_gamification_action_progression():
    user = User(id=uuid4(), username="alice")
    state = ProductState(user_id=user.id, preferences={}, achievements={})

    session = AsyncMock()
    session.get = AsyncMock(return_value=state)
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    # 1. Action: Photo
    req = GamificationActionRequest(action="photo")
    res = await record_gamification_action(req, user=user, session=session)

    # 50 base XP + 60 XP (daily_photo) + 50 XP (daily_care) = 160 earned_xp
    assert res["earned_xp"] == 160
    assert res["streak"] == 1
    assert res["level"] == 2  # 160 XP reaches Explorador (>= 100 XP)
    assert res["level_title"] == "Explorador"
    assert res["multiplier"] == 1.0
    assert res["new_xp"] == 160

    # Verify daily photo mission advanced
    photo_mission = next(m for m in res["daily_missions"] if m["id"] == "daily_photo")
    assert photo_mission["progress"] == 1
    assert photo_mission["completed"] is True

    # 2. Action: Share (Bonus mission)
    req2 = GamificationActionRequest(action="share")
    res2 = await record_gamification_action(req2, user=user, session=session)
    assert res2["bonus_mission"]["completed"] is True
    assert res2["new_xp"] > res["new_xp"]


@pytest.mark.asyncio
async def test_get_gamification_endpoint():
    user = User(id=uuid4(), username="bob")
    state = ProductState(user_id=user.id, preferences={"gamification": {"xp": 350, "streak": 3, "freeze_shields": 1}}, achievements={})

    session = AsyncMock()
    session.get = AsyncMock(return_value=state)
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    res = await get_gamification(user=user, session=session)
    assert res["xp"] == 350
    assert res["level"] == 3
    assert res["level_title"] == "Observador"
    assert res["streak"] == 3
    assert res["multiplier"] == 1.2
    assert res["total_achievements"] == 28


@pytest.mark.asyncio
async def test_journey_with_remotecat():
    from app.routers.product import journey
    from app.persistence_models import RemoteCat

    user = User(id=uuid4(), username="charlie")
    state = ProductState(user_id=user.id, preferences={}, achievements={})

    cat1 = RemoteCat(
        id=uuid4(),
        user_id=user.id,
        client_id=uuid4(),
        payload={"name": "Pantera", "primary_color": "preto", "coat_type": "curto", "is_owned": False, "captured_at": "2026-09-16T20:00:00Z"},
        deleted_at=None,
    )

    session = AsyncMock()
    session.info = {}
    session.add = MagicMock()
    session.get = AsyncMock(return_value=state)

    async def mock_execute(stmt):
        mock_res = MagicMock()
        stmt_str = str(stmt)
        if "cats" in stmt_str and "user_id" in stmt_str:
            mock_res.scalars.return_value.all.return_value = [cat1]
        elif "cat_photos" in stmt_str:
            mock_res.all.return_value = []
        elif "match_suggestions" in stmt_str:
            mock_res.scalar_one.return_value = 0
        return mock_res

    session.execute = AsyncMock(side_effect=mock_execute)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    res = await journey(user=user, session=session)
    assert len(res) == len(ACHIEVEMENTS)
    black_cat_ach = next(a for a in res if a["id"] == "black_cat")
    assert black_cat_ach["progress"] == 1
    assert black_cat_ach["unlocked_at"] is not None
    assert black_cat_ach["xp_reward"] > 0
    # Verifies that unlocking achievements awarded XP in gamification state
    assert state.preferences["gamification"]["xp"] >= black_cat_ach["xp_reward"]


@pytest.mark.asyncio
async def test_care_action_advances_daily_care():
    user = User(id=uuid4(), username="dana")
    state = ProductState(user_id=user.id, preferences={}, achievements={})

    session = AsyncMock()
    session.get = AsyncMock(return_value=state)
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    req = GamificationActionRequest(action="care")
    res = await record_gamification_action(req, user=user, session=session)

    # 25 base XP + 50 XP (daily_care mission) = 75 earned_xp
    assert res["earned_xp"] == 75
    assert "xp" in res
    assert "new_xp" in res
    assert res["xp"] == res["new_xp"]

    care_mission = next(m for m in res["daily_missions"] if m["id"] == "daily_care")
    assert care_mission["progress"] == 1
    assert care_mission["completed"] is True


