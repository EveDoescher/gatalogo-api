"""Tests for levels, streaks, missions, and achievements."""

import pytest

from app.routers.product import (
    ACHIEVEMENTS,
    LEVELS,
    calculate_level,
    calculate_multiplier,
    record_gamification_action,
    get_gamification,
    GamificationActionRequest,
)
from app.persistence_models import ProductState


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


def test_multiplier_does_not_reward_streak_pressure():
    for days in (0, 1, 2, 3, 5, 7, 30):
        assert calculate_multiplier(days) == 1.0


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


from test_progression import account

@pytest.mark.asyncio
async def test_unverified_actions_never_mint_rewards(account):
    session, user = account
    for action in ("photo", "share", "care", "like", "sighting"):
        result = await record_gamification_action(GamificationActionRequest(action=action), user=user, session=session)
        assert result["earned_xp"] == result["xp"] == result["balance"] == 0
        assert len(result["daily_missions"]) == 3
        assert len(result["weekly_missions"]) == 3

@pytest.mark.asyncio
async def test_legacy_progress_and_achievements_are_preserved(account):
    from app.routers.product import journey
    session, user = account
    session.add(ProductState(user_id=user.id, preferences={"gamification": {"xp": 350, "streak": 3}},
                             achievements={"night_owl": "2026-09-10", "first": "2026-09-10"}))
    await session.flush()
    summary = await get_gamification(user=user, session=session)
    assert (summary["xp"], summary["level"], summary["multiplier"]) == (350, 3, 1.0)
    rows = await journey(user=user, session=session)
    assert next(r for r in rows if r["id"] == "night_owl")["archived"]
    assert next(r for r in rows if r["id"] == "first")["completed"]
    assert (await get_gamification(user=user, session=session))["xp"] == 350

# Imported pytest fixtures are discovered through module globals.
__all__ = ['account']
