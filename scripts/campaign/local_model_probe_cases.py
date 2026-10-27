"""One fresh OS process per role/trial. Real cached CPU models, no paid AI."""

import asyncio
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import re
import os
from pathlib import Path
import time
from uuid import uuid4

from measurements import resources

IMPORT_RESOURCES = resources()
import httpx
import numpy as np
import pytest
from sqlalchemy import delete, func, select

pytest_plugins = ["current_offline_cases"]
from security_cases import evidence
from image_cases import semantic
from app import main
from app.config import Settings
from app.models.analysis import GeminiCoatAnalysis
from app.persistence_models import CatPhoto, CatVisualEmbedding, RemoteCat, User, VisionJob
from app.services.analysis_service import AnalysisService
from app.services import analysis_service, vision_worker
from app.services.storage_service import photo_storage
from app.services.vision_jobs import enqueue_recognition
from windows_limits import cap_private_commit


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["api", "worker"])
async def test_RSC01_real_local_models(own_api, monkeypatch, tmp_path, role):
    # This container is disposable and exclusive to RSC-01. Remove previous
    # child fixtures so the real global worker can only claim this trial's jobs.
    async with own_api.factory() as session:
        await session.execute(delete(User).where(
            User.email.like("offline-%@example.invalid"),
            User.id.not_in(own_api.ids),
        ))
        await session.commit()
    source = Path(os.environ["QUALITY_MODEL_CACHE"])
    fixture = Path(os.environ["QUALITY_MODEL_PHOTO"])
    data = fixture.read_bytes()
    photo_hash = hashlib.sha256(data).hexdigest()
    assert photo_hash == "596aa1e7cb875eb79f437e310381d26b338a81c2da23439704a73c4651e8c4bb"
    limits = cap_private_commit(2 * 1024 * 1024 * 1024)
    settings = Settings(
        _env_file=None, model_cache_dir=str(source),
        sam2_checkpoint=str(source / "sam2.1_hiera_tiny.pt"),
        vision_device="cpu", analysis_cache_dir=str(tmp_path / "analysis-cache"),
        gemini_api_key="controlled-provider-never-sent", smtp_enabled=False,
        vision_worker_poll_seconds=0.05,
    )
    network_attempts, provider_calls = [], []

    def denied(*args, **kwargs):
        network_attempts.append("external-model-or-provider-request")
        raise AssertionError("External downloads/inference prohibited in this resource experiment")

    import urllib.request
    runtime_before = resources()
    runtime_started = time.perf_counter()
    import torch
    runtime_import_seconds = time.perf_counter() - runtime_started
    runtime_after = resources()
    monkeypatch.setattr(torch.hub, "download_url_to_file", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    monkeypatch.setattr(torch.hub, "urlopen", denied)
    monkeypatch.setattr(analysis_service, "_get_gemini_client", denied)
    trace, executions = [], []

    async def sample():
        while True:
            trace.append({"monotonic": time.perf_counter(), **resources()})
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(sample())
    cold_outputs = []
    try:
        if role == "api":
            service = AnalysisService(settings)

            def controlled_semantics(image_bytes, mime_type, context):
                provider_calls.append(1)
                payload = semantic().model_dump()
                payload["color_assignments"] = [
                    {"cluster_index": int(index), "name": "Marrom"}
                    for index in re.findall(r"cluster_index=(\d+);", context)
                ]
                return GeminiCoatAnalysis.model_validate(payload)

            monkeypatch.setattr(service, "_analyze_with_gemini", controlled_semantics)
            monkeypatch.setattr(main, "AnalysisService", lambda _: service)
            client_id = uuid4()
            async with own_api.factory() as session:
                session.add(RemoteCat(
                    id=uuid4(), user_id=own_api.ids[0], client_id=client_id,
                    payload={"status": "pending_analysis"},
                    client_updated_at=datetime.now(UTC), updated_by_device="resource-model",
                ))
                await session.commit()
            async with httpx.AsyncClient(
                base_url=own_api.url, timeout=180,
                headers={"Authorization": "Bearer " + own_api.bundles[0]["access_token"]},
            ) as client:
                for index in range(4):
                    before = resources()
                    started = time.perf_counter()
                    response = await client.post(
                        "/analyze", data={"cat_id": str(client_id)},
                        files={"image": ("chelsea.png", data, "image/png")},
                    )
                    assert response.status_code == 200, response.text
                    body = response.json()
                    body.pop("analyzed_at")
                    body.pop("warnings")
                    cold_outputs.append(body)
                    executions.append({"state": "model-cold" if index == 0 else "warm",
                        "elapsed_seconds": time.perf_counter() - started,
                        "resources_before": before, "resources_after": resources(),
                        "status": response.status_code})
            assert all(value == cold_outputs[0] for value in cold_outputs)
            assert len(provider_calls) <= 1
            async with own_api.factory() as session:
                cat = await session.scalar(select(RemoteCat).where(RemoteCat.client_id == client_id))
                assert cat.photo_hash == photo_hash and photo_storage.open(cat.photo_key).read_bytes() == data
        else:
            monkeypatch.setattr(vision_worker, "get_session_factory", lambda: own_api.factory)
            service = vision_worker.Sam2DinoWorker(settings)
            jobs = []
            async with own_api.factory() as session:
                for _ in range(4):
                    cat = RemoteCat(
                        id=uuid4(), user_id=own_api.ids[0], client_id=uuid4(),
                        payload={}, analysis={"coat_type": "Rajado"},
                        client_updated_at=datetime.now(UTC), updated_by_device="resource-model",
                    )
                    session.add(cat)
                    await session.flush()
                    key, digest = photo_storage.save_reference(
                        user_id=cat.user_id, cat_id=cat.id, kind="front", data=data, suffix=".png",
                    )
                    photo = CatPhoto(id=uuid4(), cat_id=cat.id, kind="front", storage_key=key,
                        content_hash=digest, content_type="image/png", status="ready")
                    session.add(photo)
                    await session.flush()
                    job = await enqueue_recognition(session, cat=cat, photo=photo)
                    jobs.append((job.id, photo.id))
                await session.commit()
                assert await session.scalar(select(func.count()).select_from(VisionJob).where(
                    VisionJob.status.in_(("pending", "running")),
                )) == 4
            done = asyncio.Event()
            real_process = service.process
            expected_jobs = {job_id for job_id, _ in jobs}

            async def process(job_id):
                assert job_id in expected_jobs, "The worker must only claim this child fixture"
                before = resources()
                started = time.perf_counter()
                await real_process(job_id)
                async with own_api.factory() as session:
                    state = await session.get(VisionJob, job_id)
                    assert state.status == "completed" and state.attempts == 0, (state.status, state.error_message)
                executions.append({"state": "model-cold" if not executions else "warm",
                    "elapsed_seconds": time.perf_counter() - started,
                    "resources_before": before, "resources_after": resources()})
                if len(executions) == 4:
                    done.set()

            monkeypatch.setattr(service, "process", process)
            consumer = asyncio.create_task(service.run_forever())
            waiter = asyncio.create_task(done.wait())
            try:
                finished, _ = await asyncio.wait({consumer, waiter}, timeout=210, return_when=asyncio.FIRST_COMPLETED)
                assert finished, "Local model worker exceeded its bounded test deadline"
                if consumer in finished:
                    await consumer
                assert done.is_set()
            finally:
                consumer.cancel()
                waiter.cancel()
                await asyncio.gather(consumer, waiter, return_exceptions=True)
            async with own_api.factory() as session:
                for job_id, photo_id in jobs:
                    job = await session.get(VisionJob, job_id)
                    assert job.status == "completed" and job.attempts == 0, (job.status, job.error_message)
                    vector = await session.scalar(select(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == photo_id))
                    assert vector is not None and len(vector.embedding) == 384
                    values = np.array(vector.embedding)
                    assert np.isfinite(values).all() and abs(np.linalg.norm(values) - 1) < 1e-5
                    cold_outputs.append(values)
            assert all(np.allclose(value, cold_outputs[0], atol=1e-6, rtol=1e-6) for value in cold_outputs)
        assert network_attempts == []
        idle_start = time.perf_counter()
        await asyncio.sleep(5)
        after_idle = resources()
        assert own_api.factory.kw["bind"].pool.checkedout() == 0
        evidence("RSC01-local-models", {
            "scenario": "RSC-01", "role": role, "pid": os.getpid(),
            "import_resources": IMPORT_RESOURCES, "limits": limits,
            "torch_import_seconds": runtime_import_seconds,
            "resources_before_torch_import": runtime_before,
            "resources_after_torch_import": runtime_after,
            "executions": executions, "idle_seconds": time.perf_counter() - idle_start,
            "resources_after_idle": after_idle, "resource_trace": trace,
            "input_sha256": photo_hash, "paid_calls": 0,
            "controlled_semantic_calls": len(provider_calls), "external_attempts_blocked": len(network_attempts),
            "output_consistency": True, "torch_version": importlib.metadata.version("torch"),
            "torchvision_version": importlib.metadata.version("torchvision"),
            "scope": "Real Faster R-CNN/SAM2, plus DINOv2 in the actual queued worker. CPU. Semantic provider is controlled, not accuracy/cost/latency evidence for Gemini. Fresh process and empty application caches; OS file cache is not cleared. Repeated identical CC0 image is a consistency/resource fixture, not reidentification or general quality evaluation. Retained model caches are expected; five seconds idle do not certify absence of leaks.",
        })
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
