"""Bounded Windows resource caps: real API/PG, controlled reference inference."""

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

pytest.register_assert_rewrite("latency_pressure_cases", "test_reference_analysis", "security_cases")
pytest_plugins = ["test_reference_analysis", "current_offline_cases"]
from latency_pressure_cases import test_LAT08_tcp_fixed_load_during_controlled_inference as latency_probe
from security_cases import evidence
from measurements import resources
from windows_limits import affinity, cap_private_commit, set_affinity
from app.persistence_models import CatPhoto, RemoteCat, VisionJob
from app.services import reference_analysis as worker
from app.services.vision_jobs import enqueue_recognition, enqueue_reference_analysis


async def queue_probe(queue):
    expected_hash = hashlib.sha256(b"reference-photo-bytes").hexdigest()
    records = []

    async def warmup(**kwargs):
        return {"digest": expected_hash}

    assert await worker.process_next(queue.factory, settings=queue.settings, analyzer=warmup)
    async with queue.factory() as session:
        owner = (await session.get(RemoteCat, queue.cat_id)).user_id
    for consumers in (1, 2):
        for size in (1, 10, 50):
            jobs, gpu_jobs, photos, clients = [], [], [], []
            created = datetime.now(UTC)
            async with queue.factory() as session:
                for index in range(size):
                    cat = RemoteCat(
                        id=uuid4(), user_id=owner, client_id=uuid4(), payload={},
                        client_updated_at=created, updated_by_device="resource-fixture",
                    )
                    session.add(cat)
                    await session.flush()
                    photo = CatPhoto(
                        id=uuid4(), cat_id=cat.id, kind="front", storage_key="photo.jpg",
                        content_hash=expected_hash, status="analyzing",
                    )
                    session.add(photo)
                    await session.flush()
                    gpu = await enqueue_recognition(session, cat=cat, photo=photo)
                    job = await enqueue_reference_analysis(session, cat=cat, photo=photo)
                    assert (await enqueue_reference_analysis(session, cat=cat, photo=photo)).id == job.id
                    job.created_at = created + timedelta(microseconds=index)
                    jobs.append(job.id)
                    gpu_jobs.append(gpu.id)
                    photos.append(photo.id)
                    clients.append(str(cat.client_id))
                await session.commit()
            seen, inflight, peak = [], 0, 0

            async def analyze(*, cat_id, image_bytes):
                nonlocal inflight, peak
                assert hashlib.sha256(image_bytes).hexdigest() == expected_hash
                seen.append(cat_id)
                inflight += 1
                peak = max(peak, inflight)
                try:
                    await asyncio.sleep(0.002)
                    return {"digest": expected_hash}
                finally:
                    inflight -= 1

            async def consume():
                while await worker.process_next(queue.factory, settings=queue.settings, analyzer=analyze):
                    pass

            before = resources()
            started = time.perf_counter()
            await asyncio.wait_for(asyncio.gather(*(consume() for _ in range(consumers))), 60)
            elapsed = time.perf_counter() - started
            after = resources()
            assert len(seen) == size and set(seen) == set(clients) and peak <= consumers
            if consumers == 1:
                assert seen == clients, "Single-consumer FIFO priority must be preserved"
            async with queue.factory() as session:
                completed = list((await session.scalars(select(VisionJob).where(VisionJob.id.in_(jobs)))).all())
                gpu = list((await session.scalars(select(VisionJob).where(VisionJob.id.in_(gpu_jobs)))).all())
                final_photos = list((await session.scalars(select(CatPhoto).where(CatPhoto.id.in_(photos)))).all())
                assert len(completed) == len(gpu) == len(final_photos) == size
                assert all(job.status == "completed" and job.attempts == 1 for job in completed)
                assert all(job.status == "pending" and job.attempts == 0 for job in gpu)
                assert all(photo.analysis == {"digest": expected_hash} for photo in final_photos)
                timings = [{
                    "enqueue_to_claim_ms": (job.started_at - job.created_at).total_seconds() * 1000,
                    "claim_to_finish_ms": (job.completed_at - job.started_at).total_seconds() * 1000,
                    "enqueue_to_finish_ms": (job.completed_at - job.created_at).total_seconds() * 1000,
                } for job in completed]
            assert queue.factory.kw["bind"].pool.checkedout() == 0
            records.append({
                "jobs": size, "consumers": consumers, "elapsed_seconds": elapsed,
                "peak_analyzers": peak, "completed_once": size, "gpu_jobs_still_pending": size,
                "resources_before": before, "resources_after": after, "timings": timings,
            })
    return records


@pytest.mark.asyncio
@pytest.mark.parametrize("limited", [False, True], ids=["unrestricted", "cpu1-commit512MiB"])
async def test_RSC06_api_and_reference_queue_under_resource_caps(queue, own_api, monkeypatch, caplog, limited):
    assert queue.backend == "postgres", "Resource campaign selects only disposable PostgreSQL"
    original_affinity = affinity()
    condition = "cpu1-commit512MiB" if limited else "unrestricted"
    root = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / condition
    root.mkdir(parents=True)
    monkeypatch.setenv("QUALITY_CASE_EVIDENCE", str(root))
    monkeypatch.setenv("QUALITY_LATENCY_REQUESTS_PER_ROUTE", "100")
    monkeypatch.setenv("QUALITY_LATENCY_WARMUP_PER_ROUTE", "10")
    # Restore the generator's full inherited CPU mask in its own process.
    monkeypatch.setenv("QUALITY_GENERATOR_AFFINITY_MASK", str(original_affinity))
    limits = {"private_commit_limit_bytes": None, "affinity_mask": original_affinity}
    before = resources()
    try:
        if limited:
            limits.update(cap_private_commit(512 * 1024 * 1024))
            set_affinity(original_affinity & -original_affinity)
            limits["affinity_mask"] = affinity()
            # Denied by this process's job limit, without touching 512 MiB of host RAM.
            with pytest.raises(MemoryError):
                bytearray(512 * 1024 * 1024)
            limits["oversized_allocation_rejected"] = True
        batches = await queue_probe(queue)
        await latency_probe(own_api, monkeypatch, caplog)
        after = resources()
        if limited:
            assert after["private_commit_bytes"] < limits["private_commit_limit_bytes"]
        evidence("RSC06-resource-caps", {
            "scenario": "RSC-06", "condition": condition, "limits": limits,
            "resources_before": before, "resources_after": after,
            "reference_batches": batches, "paid_calls": 0,
            "scope": "One exploratory block per condition, 1/10/50 jobs x 1/2 consumers and 0/1/5 I/O-gated analyses; 100 light samples/route/phase, C5, 10 warmups/route. API and coordinator share a process. PostgreSQL has no added cap; generator restores the original CPU mask but inherits the 512 MiB per-process job ceiling. No model-quality, capacity, P99, statistical speedup or CPU-bottleneck claim.",
        })
    finally:
        set_affinity(original_affinity)
