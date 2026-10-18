from datetime import UTC, datetime
from uuid import uuid4
from types import SimpleNamespace
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.persistence_models import (User, ProductState, ProgressEvent, HabitatWallet,
    HabitatItem, HabitatTransaction, AppNotification, Friendship, RemoteCat, SyncChange, FeedItem, MissingReport, Sighting, MatchSuggestion, Conversation, ConversationMember)
from app.services import progression as p
from app.services.audience import permits
from app.quintal_models import (QuintalFacility, QuintalResource, QuintalTask,
    QuintalPlot, QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference, QuintalSession)


@pytest_asyncio.fixture
async def account(monkeypatch):
    monkeypatch.setattr(p.get_settings(), "progression_rollout_percent", 100)
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [User, ProductState, ProgressEvent, HabitatWallet, HabitatItem, HabitatTransaction, AppNotification, Friendship, RemoteCat, SyncChange, FeedItem, MissingReport, Sighting, MatchSuggestion, Conversation, ConversationMember]
    tables += [QuintalFacility, QuintalResource, QuintalTask, QuintalPlot,
               QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference, QuintalSession]
    async with engine.begin() as conn:
        for model in tables:
            await conn.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(email="progress@test.local", status="active")
        session.add(user)
        await session.flush()
        yield session, user
    await engine.dispose()


def photo(user, *, owned=False, hash="first", record=None, confidence=.9):
    cat = SimpleNamespace(user_id=user.id, client_id=record or uuid4(), payload={"is_owned": owned})
    image = SimpleNamespace(content_hash=hash, kind="primary")
    analysis = {"primary_color": "Preto", "coat_type": "Sólido", "hair_type": "Curto", "confidence": confidence}
    return cat, image, analysis


@pytest.mark.asyncio
async def test_photo_result_replay_and_hash_duplicate(account):
    db, user = account
    args = photo(user)
    event = await p.confirm_photo(db, *args)
    first = await p.snapshot(db, user.id)
    retry = await p.confirm_photo(db, *args)
    duplicate = await p.confirm_photo(db, *photo(user))
    assert retry.id == event.id
    assert duplicate.state == "ineligible"
    assert (await p.snapshot(db, user.id))["xp"] == first["xp"]
    assert event.xp == 50 and event.coins == 25
    assert len(first["daily_missions"]) == 3 and len(first["weekly_missions"]) == 3


@pytest.mark.asyncio
async def test_owned_and_uncertain_have_distinct_eligibility(account):
    db, user = account
    owned = await p.confirm_photo(db, *photo(user, owned=True))
    uncertain = await p.confirm_photo(db, *photo(user, hash="second", confidence=.3))
    assert owned.xp == owned.coins == 0
    assert uncertain.state == "needs_review" and uncertain.xp == 50
    assert not uncertain.evidence["attributes_confirmed"]


@pytest.mark.asyncio
async def test_social_limits_and_retries(account):
    db, user = account
    for index in range(5):
        await p.grant(db, user.id, f"post:{index}", "post")
    assert (await p.snapshot(db, user.id))["xp"] == 10
    assert (await p.snapshot(db, user.id))["balance"] == 3
    retry = await p.grant(db, user.id, "post:0", "post")
    assert retry.xp == 10


@pytest.mark.asyncio
async def test_opening_preserves_balances_without_paying_again(account):
    db, user = account
    db.add(ProductState(user_id=user.id, preferences={"bio": "Oi", "gamification": {"xp": 1300}}, achievements={"night_owl": "old"}))
    db.add(HabitatWallet(user_id=user.id, balance=87))
    await db.flush()
    for _ in range(2):
        state = await p.snapshot(db, user.id)
        assert state["xp"] == 1300 and state["balance"] == 87 and state["level"] == 5
    rows = (await db.scalars(select(ProgressEvent).where(ProgressEvent.kind == "opening"))).all()
    assert len(rows) == 1
    prefs = (await db.get(ProductState, user.id)).preferences
    assert prefs["bio"] == "Oi" and prefs["gallery_audience"] == "friends"


@pytest.mark.asyncio
async def test_profile_defaults_and_independent_audience(account):
    db, owner = account
    friend, stranger = uuid4(), uuid4()
    state, _ = await p.account(db, owner.id)
    assert not await permits(db, stranger, owner.id, "gallery")
    state.preferences = {**state.preferences, "gallery_audience": "public", "medals_audience": "private"}
    assert await permits(db, stranger, owner.id, "gallery")
    assert not await permits(db, stranger, owner.id, "medals")


def test_weekly_days_count_distinct_local_dates():
    when = datetime(2026, 9, 28, 1, tzinfo=UTC)
    events = [SimpleNamespace(kind="discovery", record_id=str(i), event_key=str(i), evidence={
        "eligible": True, "local_day": "2026-09-27", "local_week": "2026-09-21"}) for i in range(3)]
    missions, _, _ = p.projections(events, {"timezone": "America/Sao_Paulo"}, when)
    assert next(m for m in missions if m["id"] == "weekly_days")["progress"] == 1


async def saved_cat(db, user, **payload):
    cat = RemoteCat(user_id=user.id, client_id=uuid4(), payload=payload,
                    client_updated_at=datetime.now(UTC), updated_by_device="test")
    db.add(cat)
    await db.flush()
    return cat

@pytest.mark.asyncio
async def test_identity_can_be_reversed_without_repeated_credits(account):
    db, user = account
    a, b = await saved_cat(db, user), await saved_cat(db, user)
    await p.confirm_photo(db, a, photo(user)[1], photo(user)[2])
    event = await p.confirm_photo(db, b, photo(user, hash="other")[1], photo(user)[2])
    before = await p.snapshot(db, user.id)
    await p.identify(db, b, a.client_id)
    after = await p.snapshot(db, user.id)
    assert after["xp"] == before["xp"] - 45
    assert after["balance"] == before["balance"] - 22
    assert after["confirmed_individuals"] == 1
    assert p.result_for(event)["credits"] == {"xp": 5, "coins": 3}
    await p.identify(db, b, a.client_id)
    assert (await p.snapshot(db, user.id))["xp"] == after["xp"]
    await p.identify(db, b, b.client_id)
    assert (await p.snapshot(db, user.id))["xp"] == before["xp"]
    assert (await p.snapshot(db, user.id))["level"] >= before["level"]

@pytest.mark.asyncio
async def test_attribute_review_does_not_repeat_base_or_bonus(account):
    from app.routers.progression import review, AttributeReview
    db, user = account
    cat = await saved_cat(db, user)
    event = await p.confirm_photo(db, cat, photo(user)[1], photo(user, confidence=.2)[2])
    before = await p.snapshot(db, user.id)
    body = AttributeReview(primary_color="Branco", coat_type="Bicolor", hair_type="Curto")
    for _ in range(2):
        result = await review(event.id, body, user=user, session=db)
        assert result["state"] == "confirmed"
    assert (await p.snapshot(db, user.id))["xp"] == before["xp"] + 20  # initial review, awarded once
    assert cat.analysis["primary_color"] == "Branco"

@pytest.mark.asyncio
async def test_clue_only_pays_once_after_tutor_confirmation(account):
    from app.routers.vision import decide_match
    from fastapi import HTTPException
    db, observer = account
    tutor = User(email="tutor@test.local", username="tutor", status="active")
    db.add(tutor); await db.flush()
    cat = await saved_cat(db, tutor, is_owned=True)
    found = await saved_cat(db, observer)
    report = MissingReport(user_id=tutor.id, cat_id=cat.id, latitude=0, longitude=0, radius_meters=500)
    sighting = Sighting(observer_id=observer.id, cat_id=found.id, latitude=0, longitude=0)
    db.add_all([report, sighting]); await db.flush()
    match = MatchSuggestion(missing_report_id=report.id, sighting_id=sighting.id, similarity=.9)
    db.add(match); await db.flush()
    assert (await p.snapshot(db, observer.id))["xp"] == 0
    with pytest.raises(HTTPException):
        await decide_match(match.id, "confirm", user=observer, session=db)
    await decide_match(match.id, "confirm", user=tutor, session=db)
    assert (await p.snapshot(db, observer.id))["xp"] == 400
    assert (await p.snapshot(db, observer.id))["balance"] == 200
    with pytest.raises(HTTPException):
        await decide_match(match.id, "confirm", user=tutor, session=db)
    assert (await p.snapshot(db, observer.id))["xp"] == 400

@pytest.mark.asyncio
async def test_repost_is_linked_idempotent_and_closed_case_cannot_pay(account):
    from app.routers.social import repost_report
    from fastapi import HTTPException
    db, reader = account
    reader.username = "reader"
    tutor = User(email="owner@test.local", username="owner", status="active")
    db.add(tutor); await db.flush()
    cat = await saved_cat(db, tutor)
    report = MissingReport(user_id=tutor.id, cat_id=cat.id, latitude=0, longitude=0, radius_meters=100)
    db.add(report); await db.flush()
    with pytest.raises(HTTPException):
        await repost_report(report.id, user=tutor, session=db)
    first = await repost_report(report.id, user=reader, session=db)
    second = await repost_report(report.id, user=reader, session=db)
    assert first.id == second.id
    assert first.payload["report_id"] == str(report.id)
    assert (await p.snapshot(db, reader.id))["balance"] == 17  # action + daily + optional initial
    report.status = "resolved"; await db.flush()
    with pytest.raises(HTTPException):
        await repost_report(report.id, user=reader, session=db)

@pytest.mark.asyncio
async def test_private_post_photo_never_bypasses_post_audience(account, tmp_path, monkeypatch):
    from app.routers import social
    from fastapi import HTTPException
    from PIL import Image
    from app.services.audience import can_read_post
    db, owner = account
    reader = User(email="reader@test.local", status="active")
    db.add(reader); await db.flush()
    pid = uuid4(); filename = f"{pid}.jpg"
    image = tmp_path / f"{pid}-hash.jpg"
    Image.new("RGB", (10, 10)).save(image)
    monkeypatch.setattr(social.photo_storage, "absolute_path", lambda _: tmp_path)
    post = FeedItem(user_id=owner.id, type="cat_post", payload={"photo_url": f"/social/posts/photo/{filename}", "is_public": False})
    db.add(post); await db.flush()
    assert not await can_read_post(db, post, reader.id)
    with pytest.raises(HTTPException) as error:
        await social.get_post_photo(filename, user=reader, session=db)
    assert error.value.status_code == 404
    db.add(Friendship(user_id=reader.id, friend_id=owner.id)); await db.flush()
    response = await social.get_post_photo(filename, user=reader, session=db)
    assert response.media_type == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, no-store"

@pytest.mark.asyncio
async def test_cosmetic_unlock_is_delivered_once(account):
    db, user = account
    for index in range(2):
        await p.confirm_photo(db, *photo(user, hash=str(index)))
    items = (await db.scalars(select(HabitatItem).where(HabitatItem.purchase_key == "level:2:v1"))).all()
    assert len(items) == 1
    await p.confirm_photo(db, *photo(user, hash="1"))
    assert len((await db.scalars(select(HabitatItem).where(HabitatItem.purchase_key == "level:2:v1"))).all()) == 1


@pytest.mark.asyncio
async def test_rollout_disabled_never_replays_old_actions_when_enabled(account, monkeypatch):
    db, user = account
    monkeypatch.setattr(p.get_settings(), "progression_rollout_percent", 0)
    args = photo(user)
    event = await p.confirm_photo(db, *args)
    assert event.state == "recorded" and event.xp == event.coins == 0
    monkeypatch.setattr(p.get_settings(), "progression_rollout_percent", 100)
    repeated = await p.confirm_photo(db, *args)
    assert repeated.id == event.id and repeated.xp == 0

@pytest.mark.asyncio
async def test_history_cursor_and_experience_signal_are_isolated_and_unrewarded(account):
    from app.routers.progression import history, experience, ExperienceEvent
    from fastapi import HTTPException
    db, user = account
    for n in range(4):
        await p.grant(db, user.id, f"post:{n}", "post")
    first = await history(limit=2, cursor=None, user=user, session=db)
    second = await history(limit=2, cursor=__import__("uuid").UUID(first[-1]["event_id"]), user=user, session=db)
    assert len({e["event_id"] for e in first + second}) == 4
    from uuid import UUID
    foreign = User(email="other@test.local", status="active")
    db.add(foreign); await db.flush()
    with pytest.raises(HTTPException):
        await history(limit=2, cursor=UUID(first[0]["event_id"]), user=foreign, session=db)
    before = await p.snapshot(db, user.id)
    signal = ExperienceEvent(event_id=uuid4(), name="result_opened")
    await experience(signal, user=user, session=db)
    await experience(signal, user=user, session=db)
    after = await p.snapshot(db, user.id)
    assert before["xp"] == after["xp"] and before["balance"] == after["balance"]
    assert len((await db.scalars(select(ProgressEvent).where(ProgressEvent.kind == "experience"))).all()) == 1


@pytest.mark.asyncio
async def test_offline_batch_creates_anchor_before_linked_photo_and_audits_correction(account, monkeypatch):
    from app.routers import sync
    from app.schemas import SyncOperation
    from unittest.mock import AsyncMock
    from datetime import timedelta
    db, user = account
    monkeypatch.setattr(sync, "sync_catalog_sighting", AsyncMock())
    a, b = uuid4(), uuid4()
    now = datetime.now(UTC)
    operations = [SyncOperation(client_id=b, updated_at=now, device_id="offline", payload={"individual_id": str(a)}),
                  SyncOperation(client_id=a, updated_at=now, device_id="offline", payload={})]
    for operation in sync._ordered_operations(operations):
        await sync._apply_operation(db, user, operation)
    cats = (await db.scalars(select(RemoteCat).where(RemoteCat.user_id == user.id))).all()
    linked = next(c for c in cats if c.client_id == b)
    assert linked.payload["individual_id"] == str(a)
    event = await p.confirm_photo(db, linked, photo(user)[1], photo(user)[2])
    assert event.kind == "complement" and event.xp == 5
    correction = SyncOperation(client_id=b, updated_at=now+timedelta(seconds=1), device_id="offline",
                               payload={"individual_id": str(b)})
    await sync._apply_operation(db, user, correction)
    assert event.kind == "discovery" and p.result_for(event)["credits"]["xp"] == 50
    before = (await p.snapshot(db, user.id))["xp"]
    await sync._apply_operation(db, user, correction)
    assert (await p.snapshot(db, user.id))["xp"] == before
