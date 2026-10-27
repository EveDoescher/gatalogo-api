"""Small real-reference queue batch, supplementing the controlled 1/10/50 grid."""

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import time
import urllib.request
from uuid import uuid4

import pytest
from sqlalchemy import select

pytest_plugins = ["current_offline_cases"]
from app.config import Settings
from app.persistence_models import CatPhoto, RemoteCat, VisionJob
from app.services import analysis_service, reference_analysis as worker
from app.services.storage_service import photo_storage
from app.services.vision_jobs import enqueue_recognition, enqueue_reference_analysis
from measurements import resources
from paid_provider import PaidClient, ledger_summary
from security_cases import evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("consumers", [1, 2])
async def test_LOAD04_real_reference_queue(own_api, monkeypatch, tmp_path, consumers):
    import torch
    torch.set_num_threads(2)
    root = Path(os.environ["QUALITY_MODEL_CACHE"]).parent
    settings = Settings(_env_file=root / ".env", model_cache_dir=str(root / ".model-cache"),
        sam2_checkpoint=str(root / ".model-cache/sam2.1_hiera_tiny.pt"), vision_device="cpu")
    assert settings.vision_job_lease_seconds == 180, "Record an intentional different lease separately"
    data = Path(os.environ["QUALITY_MODEL_PHOTO"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    phase = f"LOAD04-real-consumers-{consumers}"
    ledger = Path(os.environ["QUALITY_CAMPAIGN_RUN"]) / "paid-provider-budget.sqlite3"
    client = PaidClient(api_key=settings.gemini_api_key, ledger=ledger, phase=phase)
    monkeypatch.setattr(analysis_service, "_get_gemini_client", lambda key: client)
    def denied(*args, **kwargs):
        raise AssertionError("New model downloads prohibited")
    monkeypatch.setattr(torch.hub, "download_url_to_file", denied)
    monkeypatch.setattr(torch.hub, "urlopen", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    jobs, gpu_jobs, photos, clients = [], [], [], []
    created = datetime.now(UTC)
    async with own_api.factory() as session:
        for index in range(2):
            cat = RemoteCat(id=uuid4(), user_id=own_api.ids[0], client_id=uuid4(), payload={},
                client_updated_at=created, updated_by_device="real-queue-fixture")
            session.add(cat)
            await session.flush()
            key, content_hash = photo_storage.save(user_id=cat.user_id, cat_id=cat.id, data=data, suffix=".png")
            photo = CatPhoto(id=uuid4(), cat_id=cat.id, kind="front", storage_key=key,
                content_hash=content_hash, status="analyzing")
            session.add(photo)
            await session.flush()
            gpu = await enqueue_recognition(session, cat=cat, photo=photo)
            job = await enqueue_reference_analysis(session, cat=cat, photo=photo)
            job.created_at = created + timedelta(microseconds=index)
            jobs.append(job.id)
            gpu_jobs.append(gpu.id)
            photos.append(photo.id)
            clients.append(str(cat.client_id))
        await session.commit()
    seen = []
    async def analyze(*, cat_id, image_bytes):
        assert hashlib.sha256(image_bytes).hexdigest() == digest
        seen.append(cat_id)
        # Fresh per-job caches require actual semantics, with shared real model weights.
        service = analysis_service.AnalysisService(settings.model_copy(update={
            "analysis_cache_dir": str(tmp_path / cat_id)}))
        return (await service.analyze(cat_id=cat_id, image_bytes=image_bytes)).model_dump(mode="json")
    async def consume():
        # Bounded: do not turn a provider failure into an unbounded retry/cost loop.
        for _ in range(3):
            if not await worker.process_next(own_api.factory, settings=settings, analyzer=analyze):
                return
        raise AssertionError("Real reference queue did not drain")
    before, started = resources(), time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.gather(*(consume() for _ in range(consumers))), timeout=400)
    finally:
        client.close()
    assert len(seen) == 2 and set(seen) == set(clients)
    if consumers == 1:
        assert seen == clients
    async with own_api.factory() as session:
        records = list((await session.scalars(select(VisionJob).where(VisionJob.id.in_(jobs)))).all())
        gpu = list((await session.scalars(select(VisionJob).where(VisionJob.id.in_(gpu_jobs)))).all())
        final = list((await session.scalars(select(CatPhoto).where(CatPhoto.id.in_(photos)))).all())
        assert all(j.status == "completed" and j.attempts == 1 for j in records)
        assert all(j.status == "pending" and j.attempts == 0 for j in gpu)
        assert all(p.status == "ready" and p.analysis["cat_id"] in clients for p in final)
        timings = [{"enqueue_to_claim_ms": (j.started_at-j.created_at).total_seconds()*1000,
            "claim_to_finish_ms": (j.completed_at-j.started_at).total_seconds()*1000,
            "enqueue_to_finish_ms": (j.completed_at-j.created_at).total_seconds()*1000} for j in records]
    usage = [r for r in ledger_summary(ledger) if r["phase"] == phase]
    assert len(usage) == 2 and own_api.provider_calls == []
    assert own_api.factory.kw["bind"].pool.checkedout() == 0
    evidence(f"LOAD04-real-{consumers}", {"scenario": "LOAD-04", "consumers": consumers,
        "completed_once": 2, "paid_generation_attempts": len(usage), "usage": usage,
        "timings": timings, "elapsed_seconds": time.perf_counter()-started,
        "resources_before": before, "resources_after": resources(), "input_sha256": digest,
        "model": settings.gemini_model, "lease_seconds": settings.vision_job_lease_seconds,
        "scope": "Real reference process_next, migrated own PostgreSQL and business effects, detector/SAM/Gemini. Separate DINO jobs remain pending. Two identical public CC0 photos per consumer setting; bounded consistency, not model accuracy or throughput inference."})
