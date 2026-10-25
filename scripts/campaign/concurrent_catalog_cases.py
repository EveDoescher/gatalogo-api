"""Force both real PostgreSQL lock orders for catalogue mutation vs worker finish."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from unittest.mock import AsyncMock

pytest.register_assert_rewrite(
    "security_cases", "remaining_mechanism_cases", "production_boundary_cases"
)
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW, reference_claim
from app.config import get_settings
from app.persistence_models import CatPhoto, RemoteCat, VisionJob, HabitatWallet
from app.routers import sync


async def blocked_backend(db):
    while True:
        async with db.factory() as session:
            blocked = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock'"
                )
            )
        if blocked:
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["replace", "photo-delete", "cat-delete"])
@pytest.mark.parametrize("first", ["mutation", "finish"])
async def test_DB06_overlapping_catalog_mutation_and_worker_finish(
    db, monkeypatch, action, first
):
    worker, claim = await reference_claim(db, monkeypatch)
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    monkeypatch.setattr(sync, "sync_catalog_sighting", AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()
    module, attribute = (
        (sync, "_record_change") if first == "mutation" else (worker, "record_change")
    )
    original = getattr(module, attribute)

    async def pause(session, cat):
        await original(session, cat)
        entered.set()
        await asyncio.wait_for(release.wait(), 10)

    monkeypatch.setattr(module, attribute, pause)
    new_bytes = b"bounded replacement storage bytes"

    async def mutate():
        base = f"/sync/cats/{uid('fence-client')}"
        if action == "replace":
            return await db.client.put(
                base + "/reference-photos/front",
                headers=db.headers("A"),
                files={"image": ("new.png", new_bytes, "image/png")},
            )
        if action == "photo-delete":
            return await db.client.delete(
                base + "/reference-photos/front", headers=db.headers("A")
            )
        return await db.client.post(
            "/sync/push",
            headers=db.headers("A"),
            json={
                "operations": [
                    {
                        "client_id": str(uid("fence-client")),
                        "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                        "device_id": "delete",
                        "deleted_at": datetime.now(UTC).isoformat(),
                        "payload": {},
                    }
                ]
            },
        )

    operations = {
        "mutation": mutate,
        "finish": lambda: worker._finish(
            db.factory, claim, get_settings(), incoming={"old": True}
        ),
    }
    tasks = {first: asyncio.create_task(operations[first]())}
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = "finish" if first == "mutation" else "mutation"
        tasks[second] = asyncio.create_task(operations[second]())
        await asyncio.wait_for(blocked_backend(db), 5)
        assert not any(t.done() for t in tasks.values())
    finally:
        release.set()
        results = dict(
            zip(tasks, await asyncio.wait_for(asyncio.gather(*tasks.values()), 15))
        )
    response = results["mutation"]
    assert (
        response.status_code
        == {"replace": 202, "photo-delete": 204, "cat-delete": 200}[action]
    )
    async with db.factory() as session:
        cat = await session.get(RemoteCat, claim.cat_id)
        photo = await session.get(CatPhoto, claim.photo_id)
        job = await session.get(VisionJob, claim.job_id)
        assert job.status == ("superseded" if first == "mutation" else "completed")
        assert worker.confirm_photo.await_count == (0 if first == "mutation" else 1)
        assert (await session.get(HabitatWallet, uid("A"))).balance == 100
        if action == "replace":
            assert photo.content_hash != claim.photo_hash
            assert photo.analysis is None and photo.status == "analyzing"
            assert db.storage.open(photo.storage_key).read_bytes() == new_bytes
        elif action == "photo-delete":
            assert photo.deleted_at is not None
        else:
            assert cat.deleted_at is not None
    if action != "replace":
        assert (
            await db.client.get(
                f"/sync/cats/{uid('fence-client')}/reference-photos/front",
                headers=db.headers("A"),
            )
        ).status_code == 404
    evidence(
        f"DB06-lock-{action}-{first}",
        {
            "scenario": "DB-06",
            "mutation": action,
            "first_lock_holder": first,
            "overlapping_backend_waited_on_real_lock": True,
            "old_job_status": job.status,
            "obsolete_result_did_not_override_new_resource": True,
            "progression": "spy for lock-order cases; financial contention tested separately with real transactions",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("callers", [2, 10, 25])
async def test_DB06_purchase_contention_preserves_balance_and_one_receipt(db, callers):
    from remaining_mechanism_cases import (
        test_LOAD03_DATA07_distinct_intents_compete_for_one_purchase_balance as probe,
    )

    await probe(db, callers)


@pytest.mark.asyncio
async def test_DB06_collect_contention_gives_one_reward(db):
    from production_boundary_cases import (
        test_DATA07_double_collect_different_intents_is_single_reward as probe,
    )

    await probe(db)
