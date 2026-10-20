from datetime import UTC, datetime
from types import SimpleNamespace
from app.services import milestones as m


def event(kind, key, *, day="2026-10-05", identity="cat-1", state="confirmed", **evidence):
    return SimpleNamespace(kind=kind, event_key=key, record_id=identity, state=state,
        created_at=datetime(2026, 10, 5, 15, tzinfo=UTC), evidence={"eligible": True,
            "local_day": day, "local_week": "2026-10-05", "individual_id": identity, **evidence})


def test_calendar_midnight_and_monday_are_server_owned():
    sunday = m.calendar(datetime(2026, 10, 5, 2, 59, 59, tzinfo=UTC))
    monday = m.calendar(datetime(2026, 10, 5, 3, tzinfo=UTC))
    assert sunday["day"] == "2026-10-04" and sunday["week"] == "2026-09-28"
    assert monday["week"] == "2026-10-05"
    assert monday["weekly_reset_at"] == "2026-10-12T00:00:00-03:00"


def test_shared_action_distinguishes_unique_cats_and_details():
    rows = [event("discovery", "a"), event("complement", "b"), event("encounter", "c")]
    view = m.project(rows, {}, datetime(2026, 10, 5, 15, tzinfo=UTC))
    assert len(view["daily_missions"]) == len(view["weekly_missions"]) == 3
    assert view["confirmed_individuals"] == 1
    assert view["daily_missions"][0]["progress"] == 1
    assert view["daily_missions"][1]["progress"] == 1
    assert view["weekly_missions"][1]["progress"] == 1
    assert view["initial_missions"][1]["progress"] == 1
    assert m.counters(rows)["photos"] == 2
    assert not view["challenges"] and view["season"] is None


def test_paid_objectives_never_reopen_and_legacy_medals_survive():
    rows = [event("mission", "mission:weekly_details:2026-10-05")]
    view = m.project(rows, {"preserved_achievements": {"first": "2020-01-01", "retired": "2020-01-02"}}, datetime(2026,10,5,15,tzinfo=UTC))
    assert view["weekly_missions"][1]["completed"]
    assert view["weekly_missions"][1]["progress"] == 4
    assert view["achievements"][0]["reward_received"]
    assert view["achievements"][-1]["archived"]


def test_ranking_scores_do_not_use_xp_or_initial_missions():
    rows = [event("discovery", "a"), event("complement", "b"),
        event("mission", "initial", objective_type="initial", ranking_points=9000),
        event("mission", "daily", objective_type="daily", ranking_points=10),
        event("mission", "weekly", objective_type="weekly", ranking_points=30)]
    result = m.scores(rows, {"gamification": {"xp": 999999}}, datetime(2026,10,5,15,tzinfo=UTC))
    assert result == {"cats_weekly": 1, "cats_all": 1, "missions": 40, "milestones": 0}


def test_reward_pause_preserves_activity_without_claiming_received():
    rows = [event("discovery", "a", eligible=False, activity_valid=True)]
    view = m.project(rows, {}, datetime(2026,10,5,15,tzinfo=UTC))
    assert view["confirmed_individuals"] == 1
    assert view["daily_missions"][0]["progress"] == 1
    assert not view["daily_missions"][0]["reward_received"]


def test_daily_offer_does_not_change_during_the_day():
    view = m.project([], {"milestone_daily_offers": {"2026-10-05": "care"}}, datetime(2026,10,5,15,tzinfo=UTC))
    assert view["daily_missions"][1]["id"] == "daily_care"


import pytest
from uuid import uuid4
from test_progression import account, photo, saved_cat
from app.services import progression as p


@pytest.mark.asyncio
async def test_pending_photo_requires_identity_and_confirmation_is_idempotent(account):
    db, user = account
    cat = await saved_cat(db, user, identity_state="pending")
    args = photo(user, record=cat.client_id)
    pending = await p.confirm_photo(db, cat, args[1], args[2])
    assert pending.state == "pending" and pending.xp == 0
    assert (await p.snapshot(db,user.id))["confirmed_individuals"] == 0
    await p.identify(db, cat, cat.client_id, "encounter")
    first = await p.snapshot(db,user.id)
    assert first["confirmed_individuals"] == 1
    assert first["daily_missions"][0]["completed"]
    assert first["initial_missions"][0]["completed"]
    assert first["achievements"][0]["completed"]
    await p.identify(db, cat, cat.client_id, "encounter")
    assert (await p.snapshot(db,user.id))["xp"] == first["xp"]
    assert pending.state == "resolved"


@pytest.mark.asyncio
async def test_known_cat_reencounter_does_not_award_complement(account):
    db, user = account
    known = await saved_cat(db,user)
    cat = await saved_cat(db,user,identity_state="pending")
    args = photo(user,hash="reencounter")
    await p.confirm_photo(db,cat,args[1],args[2])
    await p.identify(db,cat,known.client_id,"encounter")
    events = await p.events_for(db,user.id)
    encounter = next(e for e in events if e.kind == "encounter")
    assert encounter.xp == encounter.coins == 0
    assert not (await p.snapshot(db,user.id))["initial_missions"][1]["completed"]
    other = await saved_cat(db,user,identity_state="pending")
    await p.confirm_photo(db,other,photo(user,hash="other-angle")[1],args[2])
    await p.identify(db,other,known.client_id,"angle")
    assert (await p.snapshot(db,user.id))["weekly_missions"][1]["progress"] == 1
    assert (await p.snapshot(db,user.id))["confirmed_individuals"] == 0


@pytest.mark.asyncio
async def test_reward_rollout_off_records_validity_without_minting(account,monkeypatch):
    db,user=account
    monkeypatch.setattr(p.get_settings(),"progression_rollout_percent",0)
    event=await p.confirm_photo(db,*photo(user))
    view=await p.snapshot(db,user.id)
    assert event.xp == event.coins == view["xp"] == view["balance"] == 0
    assert view["confirmed_individuals"] == 1
    assert not view["rewards_enabled"]
    assert not view["daily_missions"][0]["reward_received"]


@pytest.mark.asyncio
async def test_useful_photos_beyond_base_cap_still_advance_objectives(account):
    db,user=account
    for i in range(4):
        cat,image,analysis=photo(user,hash=f"detail-{i}")
        cat.payload["individual_id"]="known"
        await p.confirm_photo(db,cat,image,analysis)
    view=await p.snapshot(db,user.id)
    assert view["weekly_missions"][1]["completed"]
    complements=[e for e in await p.events_for(db,user.id) if e.kind=="complement"]
    assert sorted(e.xp for e in complements) == [0,0,5,5]


@pytest.mark.asyncio
async def test_profile_highlights_only_accept_earned_medals_and_use_actual_receipt(account):
    from fastapi import HTTPException
    from app.routers.product import highlight_medals, MedalHighlights
    from app.routers.social import get_public_profile
    db,user=account
    user.username="medal_owner"
    with pytest.raises(HTTPException):
        await highlight_medals(MedalHighlights(ids=["first"]),user=user,session=db)
    await p.confirm_photo(db,*photo(user))
    await highlight_medals(MedalHighlights(ids=["first"]),user=user,session=db)
    profile=await get_public_profile("medal_owner",user=user,session=db)
    assert [row["id"] for row in profile.achievements] == ["first"]
    assert profile.achievements[0]["xp_received"] == 100
    with pytest.raises(HTTPException):
        await highlight_medals(MedalHighlights(ids=["first","first"]),user=user,session=db)
    await highlight_medals(MedalHighlights(ids=[]),user=user,session=db)
    assert not (await get_public_profile("medal_owner",user=user,session=db)).achievements


def test_completed_card_uses_historical_receipt_instead_of_new_balance_values():
    paid = event("mission","mission:weekly_details:2026-10-05")
    paid.xp, paid.coins = 30,15
    view=m.project([paid],{},datetime(2026,10,5,15,tzinfo=UTC))
    goal=view["weekly_missions"][1]
    assert goal["completed"]
    assert (goal["xp_reward"],goal["coin_reward"]) == (30,15)
    assert (goal["xp_received"],goal["coin_received"]) == (30,15)


@pytest.mark.asyncio
async def test_habitat_endpoint_advances_care_missions_once_and_rejects_invalid_target(account, monkeypatch):
    from fastapi import HTTPException
    from app.routers import habitat
    from app.services.habitat_service import wallet_for

    async def no_publish(session):
        session.info.clear()
    monkeypatch.setattr(habitat, "publish_committed_events", no_publish)
    db, user = account
    cat = await saved_cat(db, user, is_owned=True)
    wallet = await wallet_for(db, user.id)
    wallet.selected_cat_ids = [str(cat.client_id)]
    await db.commit()
    payload = habitat.InteractionRequest(action="pet", target_id=cat.client_id, idempotency_key=uuid4())
    await habitat.interaction(payload, user=user, session=db)
    first = await p.snapshot(db, user.id)
    care = next(m for m in first["daily_missions"] if m["id"] == "daily_care")
    assert care["completed"] and care["reward_received"]
    assert first["initial_missions"][3]["completed"]
    assert first["xp"] == 50 and first["balance"] == 30  # includes optional daily routine
    await habitat.interaction(payload, user=user, session=db)
    replay = await p.snapshot(db, user.id)
    assert all(first[k] == replay[k] for k in ("xp", "balance", "revision"))
    with pytest.raises(HTTPException) as error:
        await habitat.interaction(habitat.InteractionRequest(action="pet", target_id=uuid4(), idempotency_key=uuid4()), user=user, session=db)
    assert error.value.status_code == 409
    invalid = await p.snapshot(db, user.id)
    assert all(first[k] == invalid[k] for k in ("xp", "balance", "revision"))


@pytest.mark.asyncio
async def test_post_endpoint_advances_community_missions_and_replay_does_not_pay_again(account, monkeypatch):
    from app.routers import social

    async def no_publish(session):
        session.info.clear()
    monkeypatch.setattr(social, "publish_committed_events", no_publish)
    db, user = account
    user.username = "milestone_integration"
    payload = social.CatPostCreate(cat_name="Lua", note="Uma historia do nosso gato no quintal.", request_id=uuid4())
    post = await social.create_post(payload, user=user, session=db)
    first = await p.snapshot(db, user.id)
    assert first["daily_missions"][2]["completed"]
    assert first["initial_missions"][4]["completed"]
    assert first["xp"] == 40 and first["balance"] == 18
    assert first["ranking_scores"]["missions"] == 10
    replay = await social.create_post(payload, user=user, session=db)
    assert replay.id == post.id
    second = await p.snapshot(db, user.id)
    assert all(first[k] == second[k] for k in ("xp", "balance", "revision"))

# Imported pytest fixtures are discovered through module globals.
__all__ = ['account']
