"""Observe complete deterministic analysis transitions on disposable PostgreSQL."""

from datetime import timedelta
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases", "pipeline_cases", "image_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from image_cases import configured, image_bytes, semantic
from pipeline_cases import cat as seed_cat, analyze
from app import main
from app.config import get_settings
from app.persistence_models import (
    AppNotification,
    CatPhoto,
    HabitatTransaction,
    HabitatWallet,
    ProductState,
    ProgressEvent,
    RemoteCat,
    SyncChange,
    VisionJob,
)
from app.routers import sync
from app.services import progression, realtime_events, reference_analysis as worker

MODELS = (
    RemoteCat,
    CatPhoto,
    SyncChange,
    VisionJob,
    ProgressEvent,
    HabitatWallet,
    HabitatTransaction,
    ProductState,
    AppNotification,
)


async def snapshot(db):
    result = {}
    async with db.factory() as session:
        for model in MODELS:
            rows = (await session.execute(select(model.__table__))).mappings().all()
            result[model.__tablename__] = sorted(
                [json.loads(json.dumps(dict(r), default=str)) for r in rows],
                key=lambda r: json.dumps(r, sort_keys=True),
            )
        events = list(
            (
                await session.scalars(
                    select(ProgressEvent).where(ProgressEvent.user_id == uid("A"))
                )
            ).all()
        )
        wallet = await session.get(HabitatWallet, uid("A"))
        state = await session.get(ProductState, uid("A"))
        ledger = await session.scalar(
            select(func.coalesce(func.sum(HabitatTransaction.amount), 0)).where(
                HabitatTransaction.user_id == uid("A")
            )
        )
        assert wallet.balance == 100 + ledger == sum(e.coins for e in events)
        assert state.preferences["gamification"].get("xp", 0) == sum(
            e.xp for e in events
        )
        for other in ("B", "C"):
            assert (await session.get(HabitatWallet, uid(other))).balance == 100
    result["storage"] = {
        p.relative_to(db.storage.root).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in db.storage.root.rglob("*")
        if p.is_file()
    }
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("rollout", [0, 100])
async def test_DATA01_primary_metadata_reference_job_snapshots(
    db, tmp_path, monkeypatch, owned, rollout
):
    settings = get_settings()
    monkeypatch.setattr(settings, "progression_rewards_enabled", True)
    monkeypatch.setattr(settings, "progression_rollout_percent", rollout)
    await seed_cat(db)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("pipeline-cat"))
        cat.payload = {**cat.payload, "is_owned": owned}
        await progression.account(session, uid("A"))
        await session.commit()
    stages = {"initial": await snapshot(db)}
    service = configured(tmp_path, monkeypatch)
    provider = lambda *args: semantic()
    monkeypatch.setattr(service, "_analyze_with_gemini", provider)
    monkeypatch.setattr(main, "AnalysisService", lambda _: service)
    published = []
    actual_publish = realtime_events.publish_committed_events

    async def after_commit(session):
        assert not session.in_transaction(), "Publish called before transaction commit"
        published.append(True)
        await actual_publish(session)

    for module in (main, sync, worker):
        monkeypatch.setattr(module, "publish_committed_events", after_commit)
    deliveries = []

    async def observer(user, event, data):
        assert user == uid("A")
        async with db.factory() as session:
            visible = await session.get(RemoteCat, uid("pipeline-cat"))
            assert visible.analysis is not None
            if data.get("notification_id"):
                from uuid import UUID

                assert (
                    await session.get(AppNotification, UUID(data["notification_id"]))
                    is not None
                )
        deliveries.append(event)

    monkeypatch.setattr(realtime_events.ws_manager, "send_to_user", observer)
    response = await analyze(db, image_bytes())
    assert response.status_code == 200
    expected = response.json()
    stages["primary"] = await snapshot(db)
    assert len(stages["primary"]["sync_changes"]) == 1
    assert len(stages["primary"]["vision_jobs"]) == 1
    assert stages["primary"]["cats"][0]["analysis"] == expected
    assert stages["primary"]["cat_photos"][0]["analysis"] == expected
    if rollout and not owned:
        assert deliveries, "Positive control must publish committed progress"

    edited = await db.client.post(
        "/sync/push",
        headers=db.headers("A"),
        json={
            "operations": [
                {
                    "client_id": str(uid("pipeline-client")),
                    "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                    "device_id": "metadata",
                    "payload": {
                        "name": "renamed",
                        "status": "completed",
                        "is_owned": owned,
                    },
                    "analysis": {"forged": True},
                }
            ]
        },
    )
    assert edited.status_code == 200
    stages["metadata"] = await snapshot(db)
    assert len(stages["metadata"]["sync_changes"]) == 2
    assert stages["metadata"]["cats"][0]["analysis"] == expected
    assert stages["metadata"]["cats"][0]["payload"]["name"] == "renamed"
    for table in stages["primary"]:
        if table not in ("cats", "sync_changes"):
            assert stages["metadata"][table] == stages["primary"][table], table

    reference_bytes = image_bytes(size=(256, 256))
    upload = await db.client.put(
        f"/sync/cats/{uid('pipeline-client')}/reference-photos/front",
        headers=db.headers("A"),
        files={"image": ("front.png", reference_bytes, "image/png")},
    )
    assert upload.status_code == 202
    stages["reference_pending"] = await snapshot(db)
    assert len(stages["reference_pending"]["sync_changes"]) == 3
    assert len(stages["reference_pending"]["vision_jobs"]) == 3
    analyzer = AsyncMock(return_value=expected)
    assert await worker.process_next(db.factory, settings=settings, analyzer=analyzer)
    analyzer.assert_awaited_once()
    stages["reference_complete"] = await snapshot(db)
    assert len(stages["reference_complete"]["sync_changes"]) == 4
    assert len(stages["reference_complete"]["vision_jobs"]) == 3
    jobs = stages["reference_complete"]["vision_jobs"]
    assert [j["status"] for j in jobs if j["kind"] == "analysis:front"] == ["completed"]
    assert all(j["status"] == "pending" for j in jobs if j["kind"] != "analysis:front")
    photos = {p["kind"]: p for p in stages["reference_complete"]["cat_photos"]}
    assert photos["primary"]["analysis"] == expected
    assert (
        photos["front"]["analysis"] == expected and photos["front"]["status"] == "ready"
    )
    assert not await worker.process_next(
        db.factory, settings=settings, analyzer=analyzer
    )
    assert await snapshot(db) == stages["reference_complete"]
    assert len(published) == 4
    evidence(
        f"DATA01-stages-{owned}-{rollout}",
        {
            "scenario": "DATA-01",
            "owned": owned,
            "reward_rollout_percent": rollout,
            "snapshots": stages,
            "publish_checks_after_commit": len(published),
            "committed_events_observed": len(deliveries),
            "reference_inference_calls": 1,
            "scope": "Real transactions, progression, persistence, files and revisions; local fake classification, no reidentification or paid provider.",
        },
    )
