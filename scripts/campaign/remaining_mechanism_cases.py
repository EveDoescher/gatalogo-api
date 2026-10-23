"""Remaining bounded business/security mechanisms; no product changes or real IA."""

import asyncio
from io import BytesIO
import time
from unittest.mock import AsyncMock

from PIL import Image
import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from app.persistence_models import (
    CatPhoto,
    Friendship,
    HabitatWallet,
    MatchSuggestion,
    ProductState,
    RemoteCat,
)
from app.routers import vision
from measurements import resources


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [2, 10, 25])
async def test_LOAD03_DATA07_distinct_intents_compete_for_one_purchase_balance(
    db, count
):
    from app.persistence_models import HabitatItem, HabitatTransaction

    async with db.factory() as session:
        wallet = await session.get(HabitatWallet, uid("A"))
        wallet.balance = 8
        await session.commit()
    barrier = asyncio.Event()

    async def call(index):
        await barrier.wait()
        return await db.client.post(
            "/app/habitat/purchases",
            headers=db.headers("A"),
            json={
                "sku": "wood_path",
                "idempotency_key": str(uid("distinct-purchase-" + str(index))),
                "quantity": 1,
            },
        )

    calls = [asyncio.create_task(call(i)) for i in range(count)]
    barrier.set()
    responses = await asyncio.gather(*calls)
    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1 and statuses.count(409) == count - 1
    assert all(
        "Moedas insuficientes" in r.text for r in responses if r.status_code == 409
    )
    async with db.factory() as session:
        balance = (await session.get(HabitatWallet, uid("A"))).balance
        items = await session.scalar(
            select(func.count())
            .select_from(HabitatItem)
            .where(HabitatItem.user_id == uid("A"))
        )
        receipts = await session.scalar(
            select(func.count())
            .select_from(HabitatTransaction)
            .where(
                HabitatTransaction.user_id == uid("A"),
                HabitatTransaction.category == "purchase",
            )
        )
        assert balance == 0 and items == receipts == 1
        assert (await session.get(HabitatWallet, uid("B"))).balance == 100
    evidence(
        "LOAD03-distinct-balance-" + str(count),
        {
            "scenario": "LOAD-03",
            "concurrent_distinct_intents": count,
            "successes": 1,
            "insufficient_balance_conflicts": count - 1,
            "remaining_balance": balance,
            "items": items,
            "receipts": receipts,
            "scope": "real HTTP purchases, ownership cap above request count; rejection demonstrably due to funds, not order-queue capacity",
        },
    )


async def seed_cat(db, owner="B"):
    out = BytesIO()
    Image.new("RGB", (128, 128), (100, 120, 80)).save(out, format="PNG")
    data = out.getvalue()
    async with db.factory() as session:
        cat = RemoteCat(
            id=uid("mechanism-cat-" + owner),
            client_id=uid("mechanism-client-" + owner),
            user_id=uid(owner),
            client_updated_at=NOW,
            updated_by_device=owner,
            payload={
                "is_owned": True,
                "name": "Synthetic cat",
                "microchip": "private-microchip-canary",
            },
        )
        session.add(cat)
        await session.flush()
        key, digest = db.storage.save(
            user_id=uid(owner), cat_id=cat.id, data=data, suffix=".png"
        )
        cat.photo_key, cat.photo_hash, cat.photo_content_type = key, digest, "image/png"
        session.add(
            CatPhoto(
                cat_id=cat.id,
                kind="primary",
                storage_key=key,
                content_hash=digest,
                content_type="image/png",
                status="ready",
            )
        )
        await session.commit()
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["public", "friends", "private"])
@pytest.mark.parametrize("viewer", ["A", "B", "C"])
async def test_SEC04_gallery_actor_audience_matrix(db, audience, viewer):
    await seed_cat(db)
    async with db.factory() as session:
        session.add(
            ProductState(user_id=uid("B"), preferences={"gallery_audience": audience})
        )
        session.add(Friendship(user_id=uid("A"), friend_id=uid("B")))
        await session.commit()
    endpoint = f"/social/gallery/{uid('mechanism-cat-B')}/photo"
    expected = (
        200
        if viewer == "B"
        or audience == "public"
        or (viewer == "A" and audience == "friends")
        else 404
    )
    response = await db.client.get(endpoint, headers=db.headers(viewer))
    assert response.status_code == expected
    assert (await db.client.get(endpoint)).status_code == 401
    if expected == 200:
        assert response.headers["content-type"].startswith("image/")
    evidence(
        f"SEC04-gallery-{audience}-{viewer}",
        {
            "scenario": "SEC-04",
            "audience": audience,
            "viewer": viewer,
            "status": expected,
            "anonymous": 401,
        },
    )


@pytest.mark.asyncio
async def test_SEC04_gallery_preference_revoked_between_requests(db):
    await seed_cat(db)
    async with db.factory() as session:
        session.add(
            ProductState(user_id=uid("B"), preferences={"gallery_audience": "public"})
        )
        await session.commit()
    endpoint = f"/social/gallery/{uid('mechanism-cat-B')}/photo"
    assert (await db.client.get(endpoint, headers=db.headers("C"))).status_code == 200
    async with db.factory() as session:
        state = await session.get(ProductState, uid("B"))
        state.preferences = {"gallery_audience": "private"}
        await session.commit()
    assert (await db.client.get(endpoint, headers=db.headers("C"))).status_code == 404
    assert (await db.client.get(endpoint, headers=db.headers("B"))).status_code == 200
    evidence(
        "SEC04-gallery-preference-change",
        {
            "scenario": "SEC-04",
            "before": 200,
            "after_revocation": 404,
            "owner_after_revocation": 200,
            "scope": "new server requests; already downloaded bytes cannot be remotely erased",
        },
    )


@pytest.mark.asyncio
async def test_DATA09_SEC04_missing_report_sighting_and_human_decision_lifecycle(
    db, monkeypatch
):
    await seed_cat(db, "A")
    await seed_cat(db, "B")
    # Matching itself requires labeled inference and is not part of this deterministic lifecycle test.
    monkeypatch.setattr(vision, "create_report_matches", AsyncMock())
    monkeypatch.setattr(vision, "create_sighting_matches", AsyncMock())
    body = {
        "cat_client_id": str(uid("mechanism-client-B")),
        "latitude": 0,
        "longitude": 0,
        "radius_meters": 100,
        "last_seen_address": "private-address-canary",
    }
    created = await db.client.post(
        "/vision/missing-reports", headers=db.headers("B"), json=body
    )
    assert created.status_code == 201
    report = created.json()["id"]
    replay = await db.client.post(
        "/vision/missing-reports", headers=db.headers("B"), json=body
    )
    assert replay.status_code == 201 and replay.json()["id"] == report
    assert (
        await db.client.post(
            "/vision/missing-reports", headers=db.headers("A"), json=body
        )
    ).status_code == 404
    changes = {
        "options": {"address": False, "microchip": False, "qr_code": False},
        "contacts": [],
    }
    assert (
        await db.client.put(
            f"/vision/missing-reports/{report}/poster",
            headers=db.headers("B"),
            json=changes,
        )
    ).status_code == 200
    public = await db.client.get(f"/social/reports/{report}", headers=db.headers("C"))
    assert (
        public.status_code == 200
        and "private-address-canary" not in public.text
        and "private-microchip-canary" not in public.text
    )
    sighting_body = {
        "cat_client_id": str(uid("mechanism-client-A")),
        "client_event_id": "same-event",
        "latitude": 1,
        "longitude": 1,
    }
    first = await db.client.post(
        "/vision/sightings", headers=db.headers("A"), json=sighting_body
    )
    second = await db.client.post(
        "/vision/sightings", headers=db.headers("A"), json=sighting_body
    )
    assert (
        first.status_code == second.status_code == 201
        and first.json()["id"] == second.json()["id"]
    )
    assert (
        await db.client.post(
            "/vision/sightings",
            headers=db.headers("A"),
            json={**sighting_body, "latitude": 2},
        )
    ).status_code == 409
    from uuid import UUID

    async with db.factory() as session:
        match = MatchSuggestion(
            id=uid("mechanism-match"),
            missing_report_id=UUID(report),
            sighting_id=UUID(first.json()["id"]),
            similarity=0.8,
            restricted=False,
            evidence={"synthetic": True},
        )
        session.add(match)
        await session.commit()
    endpoint = f"/vision/matches/{uid('mechanism-match')}"
    assert (
        await db.client.post(endpoint + "/confirm", headers=db.headers("C"))
    ).status_code == 404
    dismissed = await db.client.post(endpoint + "/dismiss", headers=db.headers("B"))
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    assert (
        await db.client.post(endpoint + "/reopen", headers=db.headers("B"))
    ).status_code == 200
    confirmed = await db.client.post(endpoint + "/confirm", headers=db.headers("B"))
    assert (
        confirmed.status_code == 200
        and confirmed.json()["status"] == "confirmed"
        and confirmed.json()["latitude"] == 1
    )
    assert (
        await db.client.post(endpoint + "/confirm", headers=db.headers("B"))
    ).status_code == 409
    assert (
        await db.client.post(
            f"/vision/missing-reports/{report}/resolve", headers=db.headers("C")
        )
    ).status_code == 404
    assert (
        await db.client.post(
            f"/vision/missing-reports/{report}/resolve", headers=db.headers("B")
        )
    ).status_code == 204
    closed = await db.client.get(f"/social/reports/{report}", headers=db.headers("C"))
    assert closed.status_code == 200 and closed.json()["photo_url"] is None
    assert (
        await db.client.put(
            f"/vision/missing-reports/{report}/poster",
            headers=db.headers("B"),
            json=changes,
        )
    ).status_code == 409
    evidence(
        "DATA09-report-lifecycle",
        {
            "scenario": "DATA-09",
            "report_idempotent": True,
            "sighting_idempotent": True,
            "conflicting_event": 409,
            "human_decisions": ["dismiss", "reopen", "confirm"],
            "duplicate_confirmation": 409,
            "closed_poster_edit": 409,
            "public_private_canaries_absent": True,
            "scope": "synthetic match exercises human decision workflow, not confirmed cat reidentification accuracy",
        },
    )


@pytest.mark.asyncio
async def test_SEC09_local_configuration_http_surface(db):
    from app.config import Settings

    docs = await db.client.get("/docs")
    schema = await db.client.get("/openapi.json")
    cors = await db.client.options(
        "/sync/pull",
        headers={
            "Origin": "https://untrusted.example.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert schema.status_code == 200 and "paths" in schema.json()
    assert "access-control-allow-origin" not in cors.headers

    evidence(
        "SEC09-local-surface",
        {
            "scenario": "SEC-09",
            "docs_http": docs.status_code,
            "openapi_http": schema.status_code,
            "cross_origin_preflight_http": cors.status_code,
            "cross_origin_allow_header": False,
            "default_secret_is_development_placeholder": Settings.model_fields[
                "jwt_secret"
            ].default.startswith("development-"),
            "default_otp_pepper_is_development_placeholder": Settings.model_fields[
                "otp_pepper"
            ].default.startswith("development-"),
            "scope": "frozen app defaults and disposable service only; deployment/reverse-proxy/release settings not certified",
        },
    )


@pytest.mark.asyncio
async def test_RSC02_bounded_repeated_read_error_cycles(db):
    await seed_cat(db)
    samples = []
    baseline = resources()
    started = time.monotonic()
    path = f"/sync/cats/{uid('mechanism-client-B')}/photo"
    for round_number in range(3):
        for index in range(100):
            for endpoint, expected in [
                ("/sync/pull", 200),
                (path, 200),
                ("/social/conversations", 200),
                ("/sync/pull?limit=501", 422),
            ]:
                response = await db.client.get(endpoint, headers=db.headers("B"))
                assert response.status_code == expected
            if index in [0, 24, 49, 74, 99]:
                sample = resources()
                samples.append({"round": round_number, "cycles": index + 1, **sample})
                assert time.monotonic() - started < 120, (
                    "bounded test wall-clock budget reached"
                )
                if sample.get("rss_bytes") and baseline.get("rss_bytes"):
                    assert sample["rss_bytes"] - baseline["rss_bytes"] < 1024**3, (
                        "own-process memory safety budget reached"
                    )
        await asyncio.sleep(0.1)
    evidence(
        "RSC02-read-error-cycles",
        {
            "scenario": "RSC-02",
            "rounds": 3,
            "cycles_per_round": 100,
            "requests": 1200,
            "samples": samples,
            "interpretation": "ASGI test-process resource observations; no upload/write/conversation-send loop or server/mobile leak certification",
        },
    )
