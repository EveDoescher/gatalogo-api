"""Human report decisions: exact durable effects, never visual identity claims."""

import asyncio
from uuid import UUID
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases", "remaining_mechanism_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid
from remaining_mechanism_cases import seed_cat
from app.config import get_settings
from app.persistence_models import (
    AppNotification,
    Conversation,
    ConversationMember,
    Friendship,
    HabitatTransaction,
    HabitatWallet,
    MatchSuggestion,
    MissingReport,
    ProgressEvent,
    Sighting,
)
from app.routers import vision, product
from app.services import realtime_events


async def count(db, model, *conditions):
    async with db.factory() as session:
        return await session.scalar(
            select(func.count()).select_from(model).where(*conditions)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout", [0, 100])
@pytest.mark.parametrize("friends", [False, True])
async def test_DATA09_report_transition_rewards_notifications_and_race(
    db, monkeypatch, rollout, friends
):
    settings = get_settings()
    monkeypatch.setattr(settings, "progression_rewards_enabled", True)
    monkeypatch.setattr(settings, "progression_rollout_percent", rollout)
    await seed_cat(db, "A")
    await seed_cat(db, "B")
    monkeypatch.setattr(vision, "create_report_matches", AsyncMock())
    monkeypatch.setattr(vision, "create_sighting_matches", AsyncMock())
    monkeypatch.setattr(product, "create_report_matches", AsyncMock())
    actual_publish = realtime_events.publish_committed_events
    published = []

    async def after_commit(session):
        assert not session.in_transaction()
        await actual_publish(session)

    for module in (vision, product):
        monkeypatch.setattr(module, "publish_committed_events", after_commit)

    async def observer(user, event, data):
        assert user == uid("A") and event == "new_notification"
        async with db.factory() as session:
            notice = await session.get(AppNotification, UUID(data["notification_id"]))
            assert notice is not None and notice.type == "match_confirmed"
            match = await session.get(MatchSuggestion, UUID(notice.payload["match_id"]))
            assert match.status == "confirmed"
        published.append(event)

    monkeypatch.setattr(realtime_events.ws_manager, "send_to_user", observer)
    body = {
        "cat_client_id": str(uid("mechanism-client-B")),
        "latitude": 0,
        "longitude": 0,
        "radius_meters": 100,
    }
    created = await db.client.post(
        "/vision/missing-reports", headers=db.headers("B"), json=body
    )
    assert created.status_code == 201
    report = created.json()["id"]
    repeated = await db.client.post(
        "/vision/missing-reports", headers=db.headers("B"), json=body
    )
    assert repeated.status_code == 201 and repeated.json()["id"] == report
    assert await count(db, MissingReport) == 1
    edited = await db.client.put(
        f"/app/reports/{report}",
        headers=db.headers("B"),
        json={
            "latitude": 1,
            "longitude": 1,
            "radius_meters": 200,
            "last_seen_address": "synthetic updated area",
        },
    )
    assert edited.status_code == 200
    async with db.factory() as session:
        row = await session.get(MissingReport, UUID(report))
        assert (row.latitude, row.longitude, row.radius_meters) == (1, 1, 200)
    sighting = {
        "cat_client_id": str(uid("mechanism-client-A")),
        "client_event_id": "same-intent",
        "latitude": 1,
        "longitude": 1,
    }
    first = await db.client.post(
        "/vision/sightings", headers=db.headers("A"), json=sighting
    )
    replay = await db.client.post(
        "/vision/sightings", headers=db.headers("A"), json=sighting
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert await count(db, Sighting) == 1
    assert await count(db, ProgressEvent, ProgressEvent.kind == "sighting") == 1
    conflict = await db.client.post(
        "/vision/sightings", headers=db.headers("A"), json={**sighting, "latitude": 2}
    )
    assert conflict.status_code == 409 and await count(db, Sighting) == 1
    async with db.factory() as session:
        session.add(
            MatchSuggestion(
                id=uid("lifecycle-match"),
                missing_report_id=UUID(report),
                sighting_id=UUID(first.json()["id"]),
                similarity=0.8,
                restricted=False,
                evidence={"synthetic": True},
            )
        )
        if friends:
            session.add(Friendship(user_id=uid("B"), friend_id=uid("A")))
        await session.commit()
    endpoint = f"/vision/matches/{uid('lifecycle-match')}"
    assert (
        await db.client.post(endpoint + "/confirm", headers=db.headers("C"))
    ).status_code == 404
    dismissed = await db.client.post(endpoint + "/dismiss", headers=db.headers("B"))
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    assert (
        await db.client.post(endpoint + "/dismiss", headers=db.headers("B"))
    ).status_code == 409
    assert await count(db, AppNotification) == 0
    assert await count(db, ProgressEvent, ProgressEvent.kind == "clue") == 0
    reopened = await db.client.post(endpoint + "/reopen", headers=db.headers("B"))
    assert reopened.status_code == 200 and reopened.json()["status"] == "pending"
    assert reopened.json()["latitude"] is None
    gate = asyncio.Event()

    async def confirm():
        await gate.wait()
        return await db.client.post(endpoint + "/confirm", headers=db.headers("B"))

    tasks = [asyncio.create_task(confirm()) for _ in range(2)]
    gate.set()
    results = await asyncio.gather(*tasks)
    assert sorted(r.status_code for r in results) == [200, 409]
    assert [r.json()["latitude"] for r in results if r.status_code == 200] == [1]
    async with db.factory() as session:
        grants = list(
            (
                await session.scalars(
                    select(ProgressEvent).where(ProgressEvent.kind == "clue")
                )
            ).all()
        )
        assert len(grants) == 1 and grants[0].user_id == uid("A")
        assert (grants[0].xp, grants[0].coins) == ((100, 50) if rollout else (0, 0))
        ledger = await session.scalar(
            select(func.coalesce(func.sum(HabitatTransaction.amount), 0)).where(
                HabitatTransaction.user_id == uid("A")
            )
        )
        assert (await session.get(HabitatWallet, uid("A"))).balance == 100 + ledger
        assert (await session.get(HabitatWallet, uid("C"))).balance == 100
    assert (
        await count(db, AppNotification, AppNotification.type == "match_confirmed") == 1
    )
    assert published == ["new_notification"]
    assert await count(db, Conversation) == int(friends)
    assert await count(db, ConversationMember) == 2 * int(friends)
    for _ in range(2):
        assert (
            await db.client.post(
                f"/vision/missing-reports/{report}/resolve", headers=db.headers("B")
            )
        ).status_code == 204
    assert (
        await db.client.post(endpoint + "/confirm", headers=db.headers("B"))
    ).status_code == 409
    assert (
        str(report)
        not in (await db.client.get("/social/reports", headers=db.headers("C"))).text
    )
    details = await db.client.get(f"/social/reports/{report}", headers=db.headers("C"))
    assert details.status_code == 200 and details.json()["photo_url"] is None
    assert (await db.client.get(f"/public/busca/{report}/foto")).status_code == 404
    assert await count(db, ProgressEvent, ProgressEvent.kind == "clue") == 1
    assert (
        await count(db, AppNotification, AppNotification.type == "match_confirmed") == 1
    )
    assert published == ["new_notification"]
    evidence(
        f"DATA09-transition-{rollout}-{friends}",
        {
            "scenario": "DATA-09",
            "reward_rollout": rollout,
            "friends": friends,
            "sightings": 1,
            "sighting_intents": 1,
            "confirmation_statuses": [200, 409],
            "clue_grants": 1,
            "confirmed_notifications": 1,
            "conversation_count": int(friends),
            "committed_events": len(published),
            "resolved_media": 404,
            "scope": "Synthetic suggestion tests human decision and ledger; similarity is not evidence of actual cat reidentification.",
        },
    )
