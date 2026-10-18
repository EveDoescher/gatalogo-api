from datetime import UTC, datetime, timedelta
from sqlalchemy import select
import pytest
from app.services import milestones as m, progression as p
from app.persistence_models import ProgressEvent
from app.quintal_models import QuintalFacility
from test_progression import account
from test_milestones_v2 import event

NOW = datetime(2026, 10, 7, 15, tzinfo=UTC)

def routines(view):
    return [r for r in view["daily_missions"] + view["weekly_missions"]
            if r.get("group") == "quintal_routine"]

def test_locked_spaces_never_become_routine_requirements():
    assert not routines(m.project([], {}, NOW))
    view = m.project([], {"quintal_routine_spaces": ["garden"]}, NOW)
    assert view["daily_missions"][0]["id"] == "daily_record"
    assert view["weekly_missions"][0]["id"] == "weekly_days"
    for row in routines(view):
        assert row["optional"] and row["selection_mode"] == "any"
        assert [o["space"] for o in row["alternatives"]] == ["garden"]
        assert row["tutorial_anchor"]
        assert row["ranking_points"] == 0


def test_one_choice_finishes_day_and_two_nonconsecutive_days_finish_week():
    prefs = {"quintal_routine_spaces": ["garden", "coop", "kitchen", "pier"]}
    rows = [event("quintal", "eggs", day="2026-10-07", metric="quintal_egg", quantity=8),
            event("quintal", "harvest", day="2026-10-07", metric="quintal_harvest", quantity=1)]
    daily, weekly = routines(m.project(rows, prefs, NOW))
    assert daily["progress"] == 1 and weekly["progress"] == 1
    rows.append(event("quintal", "monday", day="2026-10-05", metric="quintal_harvest", quantity=1))
    daily, weekly = routines(m.project(rows, prefs, NOW))
    assert weekly["progress"] == 2
    assert daily["target"] == 1 and weekly["target"] == 2
    assert not daily["reward_received"]  # projection never grants coins


def test_calendar_legacy_invalid_and_locked_activity_do_not_fabricate_progress():
    prefs = {"quintal_routine_spaces": ["garden"]}
    rows = [event("quintal", "old", day=None, metric="quintal_harvest", quantity=999, historical_undated=True),
            event("quintal", "invalid", day="2026-10-07", metric="quintal_harvest", quantity=1, eligible=False),
            event("quintal", "locked", day="2026-10-07", metric="quintal_fish", quantity=1),
            event("quintal", "sunday", day="2026-10-04", local_week="2026-09-28", metric="quintal_harvest", quantity=1)]
    assert all(r["progress"] == 0 for r in routines(m.project(rows, prefs, NOW)))
    rows.append(event("quintal", "valid", day="2026-10-07", metric="quintal_harvest", quantity=1))
    assert routines(m.project(rows, prefs, NOW))[0]["progress"] == 1
    assert routines(m.project(rows, prefs, NOW + timedelta(days=1)))[0]["progress"] == 0
    assert routines(m.project(rows, prefs, NOW + timedelta(days=5)))[1]["progress"] == 0


@pytest.mark.asyncio
async def test_unlocks_and_expansion_preserve_offer_and_reward_replay(account):
    db, user = account
    state, _ = await p.account(db, user.id)
    state.preferences = {**state.preferences, "quintal_routine_spaces": ["pier"]}
    garden = QuintalFacility(user_id=user.id, kind="garden", level=0, target_level=1)
    db.add(garden)
    await db.flush()
    assert not routines(await p.snapshot(db, user.id))  # forged preference and ongoing repair ignored
    garden.level = 1
    garden.target_level = 2
    await db.flush()
    first = routines(await p.snapshot(db, user.id))
    assert all([o["space"] for o in r["alternatives"]] == ["garden"] for r in first)
    activity = await p.grant(db, user.id, "routine-harvest", "quintal", xp=0, coins=0,
                             evidence={"metric": "quintal_harvest", "quantity": 1})
    await p.finish(db, activity)
    snapshot = await p.snapshot(db, user.id)
    assert routines(snapshot)[0]["completed"]
    before = snapshot["xp"], snapshot["balance"]
    # Retry and a second valid action do not pay the daily reward twice.
    await p.finish(db, activity)
    other = await p.grant(db, user.id, "routine-harvest-2", "quintal", xp=0, coins=0,
                         evidence={"metric": "quintal_harvest", "quantity": 1})
    await p.finish(db, other)
    garden.level = 2
    garden.target_level = 0
    await db.flush()
    snapshot = await p.snapshot(db, user.id)
    assert (snapshot["xp"], snapshot["balance"]) == before
    assert routines(snapshot)[0]["instance_id"] == first[0]["instance_id"]
    paid = list((await db.scalars(select(ProgressEvent).where(
        ProgressEvent.user_id == user.id, ProgressEvent.event_key.like("mission:daily_quintal_routine:%")))).all())
    assert len(paid) == 1 and paid[0].xp == 10 and paid[0].coins == 5
    uid = user.id
    await db.commit()
    db.expire_all()
    assert routines(await p.snapshot(db, uid))[0]["completed"]


@pytest.mark.asyncio
async def test_weekly_reward_is_capped(account, monkeypatch):
    db, user = account
    db.add(QuintalFacility(user_id=user.id, kind="garden", level=1))
    await db.flush()
    calendar = m.calendar()
    # Events are dated by the server; no dependency on the weekday running this test.
    current = datetime.fromisoformat(calendar["server_now"])
    monday = current - timedelta(days=current.weekday())
    monkeypatch.setattr(m, "calendar", lambda now=None: {
        **calendar, "day": (monday + timedelta(days=2)).date().isoformat()})
    for index in range(3):
        day = (monday + timedelta(days=index)).date().isoformat()
        row = await p.grant(db, user.id, f"weekly-{index}", "quintal", xp=0, coins=0,
                           evidence={"metric": "quintal_harvest", "quantity": 1,
                                     "local_day": day, "local_week": calendar["week"]})
        await p.finish(db, row)
    view = await p.snapshot(db, user.id)
    assert routines(view)[1]["completed"]
    bonuses = [e for e in await p.events_for(db, user.id) if e.event_key.startswith("mission:weekly_quintal_routine:")]
    assert len(bonuses) == 1 and (bonuses[0].xp, bonuses[0].coins) == (30, 15)


@pytest.mark.asyncio
async def test_paused_rewards_keep_optional_progress_without_paying(account, monkeypatch):
    db, user = account
    db.add(QuintalFacility(user_id=user.id, kind="garden", level=1))
    await db.flush()
    monkeypatch.setattr(p.get_settings(), "progression_rollout_percent", 0)
    activity = await p.grant(db, user.id, "paused-routine", "quintal", xp=0, coins=0,
                             evidence={"metric": "quintal_harvest", "quantity": 1})
    await p.finish(db, activity)
    view = await p.snapshot(db, user.id)
    routine = routines(view)[0]
    assert routine["progress"] == 1 and not routine["reward_received"]
    assert view["xp"] == view["balance"] == 0
    assert not [e for e in await p.events_for(db, user.id)
                if e.event_key.startswith("mission:daily_quintal_routine:")]
