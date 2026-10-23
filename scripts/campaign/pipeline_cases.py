"""HTTP photo flow with real persistence/cache/preprocessing and fake inference."""

import asyncio
from datetime import timedelta
from io import BytesIO
import threading
from types import SimpleNamespace

from PIL import Image
import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases", "image_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from image_cases import configured, image_bytes, semantic
from app import main
from app.models.vision import SegmentationResult
from app.persistence_models import CatPhoto, ProgressEvent, RemoteCat, VisionJob
from app.services.quality_service import QualityService


async def cat(db):
    async with db.factory() as session:
        session.add(
            RemoteCat(
                id=uid("pipeline-cat"),
                client_id=uid("pipeline-client"),
                user_id=uid("A"),
                client_updated_at=NOW,
                updated_by_device="a",
                payload={"status": "pending_analysis", "is_owned": True},
            )
        )
        await session.commit()


async def analyze(db, data):
    return await db.client.post(
        "/analyze",
        headers=db.headers("A"),
        data={"cat_id": str(uid("pipeline-client"))},
        files={"image": ("cat.png", data, "image/png")},
    )


@pytest.mark.asyncio
async def test_DATA01_IMG02_HTTP_photo_metadata_sync_preserves_analysis(
    db, tmp_path, monkeypatch
):
    await cat(db)
    service = configured(tmp_path, monkeypatch)
    calls = []

    def provider(*args):
        calls.append(1)
        return semantic()

    monkeypatch.setattr(service, "_analyze_with_gemini", provider)
    monkeypatch.setattr(main, "AnalysisService", lambda settings: service)
    response = await analyze(db, image_bytes())
    assert response.status_code == 200
    expected = response.json()
    async with db.factory() as session:
        stored = await session.get(RemoteCat, uid("pipeline-cat"))
        assert stored.analysis == expected and stored.payload["status"] == "completed"
        primary = await session.scalar(
            select(CatPhoto).where(
                CatPhoto.cat_id == stored.id, CatPhoto.kind == "primary"
            )
        )
        assert (
            primary.analysis == expected
            and db.storage.open(primary.storage_key).read_bytes() == image_bytes()
        )
        assert await session.scalar(select(func.count()).select_from(VisionJob)) == 1
    push = await db.client.post(
        "/sync/push",
        headers=db.headers("A"),
        json={
            "operations": [
                {
                    "client_id": str(uid("pipeline-client")),
                    "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                    "device_id": "a",
                    "payload": {
                        "name": "Renamed synthetic cat",
                        "is_owned": True,
                        "status": "completed",
                    },
                    "analysis": {"forged": True},
                }
            ]
        },
    )
    assert push.status_code == 200
    pull = await db.client.get("/sync/pull", headers=db.headers("A"))
    assert pull.status_code == 200 and len(pull.json()["changes"]) == 1
    record = pull.json()["changes"][0]
    assert (
        record["analysis"] == expected
        and record["photo_available"]
        and record["payload"]["name"] == "Renamed synthetic cat"
    )
    assert len(calls) == 1
    evidence(
        "DATA01-photo-sync",
        {
            "scenario": "DATA-01",
            "analysis_response_equals_persisted_primary_and_catalog": True,
            "metadata_changed": True,
            "forged_analysis_ignored": True,
            "photo_available_after_sync": True,
            "pending_recognition_jobs": 1,
            "provider_calls": 1,
            "scope": "HTTP/server and fresh DB sessions; mobile local restart is a separate scenario",
        },
    )


@pytest.mark.asyncio
async def test_LAT08_DB05_paused_inference_allows_health_sync_and_chat(
    db, tmp_path, monkeypatch
):
    await cat(db)
    service = configured(tmp_path, monkeypatch)
    entered, released = threading.Event(), threading.Event()

    def provider(*args):
        entered.set()
        if not released.wait(10):
            raise TimeoutError("test inference barrier expired")
        return semantic()

    monkeypatch.setattr(service, "_analyze_with_gemini", provider)
    monkeypatch.setattr(main, "AnalysisService", lambda settings: service)
    pending = asyncio.create_task(analyze(db, image_bytes()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        responses = await asyncio.wait_for(
            asyncio.gather(
                *[
                    db.client.get(path, headers=db.headers("C"))
                    for path in ["/health", "/sync/pull", "/social/conversations"]
                ]
            ),
            3,
        )
        assert [r.status_code for r in responses] == [
            200,
            200,
            200,
        ] and not pending.done()
        assert db.engine.pool.checkedout() == 0
    finally:
        released.set()
        result = await asyncio.wait_for(pending, 10)
    assert result.status_code == 200
    evidence(
        "LAT08-paused-inference",
        {
            "scenario": "LAT-08",
            "parallel_health": 200,
            "parallel_sync": 200,
            "parallel_chat": 200,
            "held_database_connections_at_barrier": 0,
            "analysis_after_release": 200,
            "scope": "single bounded functional barrier with fake provider, not capacity or latency percentile evidence",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect,reason",
    [
        ("no-cat", "NOT_A_CAT"),
        ("multiple", "MULTIPLE_CATS"),
        ("dark", "LOW_QUALITY"),
        ("white", "LOW_QUALITY"),
    ],
)
async def test_IMG05_HTTP_rejection_preserves_business_effects(
    db, tmp_path, monkeypatch, defect, reason
):
    await cat(db)
    service = configured(tmp_path, monkeypatch)
    calls = []

    def provider(*args):
        calls.append(1)
        return semantic()

    monkeypatch.setattr(service, "_analyze_with_gemini", provider)
    data = image_bytes()
    if defect in {"no-cat", "multiple"}:
        service.segmentation = SimpleNamespace(
            segment_cat=lambda image: SegmentationResult(
                0 if defect == "no-cat" else 2,
                0.95,
                None,
                None,
                None,
                ambiguous_multiple_cats=defect == "multiple",
            )
        )
    else:
        service.quality = QualityService(service.settings)
        out = BytesIO()
        Image.new(
            "RGB", (256, 128), (0, 0, 0) if defect == "dark" else (255, 255, 255)
        ).save(out, format="PNG")
        data = out.getvalue()
    monkeypatch.setattr(main, "AnalysisService", lambda settings: service)
    first = await analyze(db, data)
    second = await analyze(db, data)
    assert (
        first.status_code == second.status_code == 422
        and first.json()["detail"]["code"] == reason
    )
    assert calls == []
    async with db.factory() as session:
        stored = await session.get(RemoteCat, uid("pipeline-cat"))
        assert stored.analysis is None
        assert await session.scalar(select(func.count()).select_from(CatPhoto)) == 0
        events = (
            await session.scalars(
                select(ProgressEvent).where(
                    ProgressEvent.user_id == uid("A"), ProgressEvent.kind == "discovery"
                )
            )
        ).all()
        assert len(events) == 1 and events[0].evidence["eligible"] is False
    evidence(
        "IMG05-http-" + defect,
        {
            "scenario": "IMG-05",
            "variant": defect,
            "status": 422,
            "code": reason,
            "provider_calls": 0,
            "duplicate_request_single_rejected_eligibility_record": True,
            "no_accepted_photo_or_analysis": True,
            "scope": "real exposure check for dark/white; detection/multiple-cat predictions are fake",
        },
    )


def test_IMG05_blur_alone_follows_existing_quality_rule():
    import numpy as np
    from app.config import Settings

    metrics = QualityService(Settings(_env_file=None)).analyze(
        np.full((128, 128, 3), 128, dtype=np.uint8)
    )
    assert metrics.blur_score == 0 and metrics.usable
    evidence(
        "IMG05-blur-rule",
        {
            "scenario": "IMG-05",
            "blur_score": metrics.blur_score,
            "quality_usable": metrics.usable,
            "scope": "current quality rule accepts blur alone; no claim this generated image depicts a cat",
        },
    )
