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

    assert res["earned_xp"] == 50
    assert res["streak"] == 1
    assert res["level"] == 2  # 160 XP reaches Explorador (>= 100 XP)
    assert res["level_title"] == "Explorador"
    assert res["multiplier"] == 1.0
    # 50 base XP + 60 XP (daily_photo) + 50 XP (daily_care) = 160 XP
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
