"""Pause all three analysis paths and inspect their own database pools."""

import asyncio
import threading

import pytest
from sqlalchemy import event

pytest.register_assert_rewrite(
    "security_cases", "pipeline_cases", "test_reference_analysis"
)
pytest_plugins = ["security_cases", "test_reference_analysis"]
from security_cases import evidence
from app.persistence_models import VisionJob, CatPhoto
from app.services import reference_analysis as worker, vision_worker


def monitor(engine):
    counts = {"checkout": 0, "checkin": 0}

    def checkout(*args):
        counts["checkout"] += 1

    def checkin(*args):
        counts["checkin"] += 1

    event.listen(engine.sync_engine, "checkout", checkout)
    event.listen(engine.sync_engine, "checkin", checkin)

    def stop():
        event.remove(engine.sync_engine, "checkout", checkout)
        event.remove(engine.sync_engine, "checkin", checkin)

    return counts, stop


@pytest.mark.asyncio
async def test_DB05_primary_analysis_releases_connection_and_serves_parallel_requests(
    db, tmp_path, monkeypatch
):
    from pipeline_cases import (
        test_LAT08_DB05_paused_inference_allows_health_sync_and_chat as probe,
    )

    counts, stop = monitor(db.engine)
    try:
        await probe(db, tmp_path, monkeypatch)
        assert counts["checkout"] == counts["checkin"] > 0
        evidence(
            "DB05-primary-pool",
            {
                "scenario": "DB-05",
                "flow": "primary",
                "pool_events": dict(counts),
                "held_at_inference_barrier": 0,
                "parallel_http_statuses": [200, 200, 200],
            },
        )
    finally:
        stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["postgres"], indirect=True)
@pytest.mark.parametrize("flow", ["reference", "recognition"])
async def test_DB05_worker_analysis_releases_connection_during_parallel_requests(
    db, queue, monkeypatch, flow
):
    engine = queue.factory.kw["bind"]
    counts, stop = monitor(engine)
    entered, released = threading.Event(), threading.Event()

    def pause():
        assert engine.pool.checkedout() == 0
        entered.set()
        if not released.wait(10):
            raise TimeoutError("Bounded inference barrier expired")

    if flow == "reference":

        async def analyzer(**kwargs):
            await asyncio.to_thread(pause)
            return {"coat_type": "fixture"}

        pending = asyncio.create_task(
            worker.process_next(
                queue.factory, settings=queue.settings, analyzer=analyzer
            )
        )
    else:
        monkeypatch.setattr(vision_worker, "get_session_factory", lambda: queue.factory)
        gpu = object.__new__(vision_worker.Sam2DinoWorker)
        gpu.settings = queue.settings

        def inspect(data):
            pause()
            raise ValueError("controlled inference error after pool observation")

        gpu.extract_bytes = inspect
        pending = asyncio.create_task(gpu.process(queue.gpu_id))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert counts["checkout"] == counts["checkin"] > 0
        at_barrier = dict(counts)
        results = await asyncio.wait_for(
            asyncio.gather(
                *[
                    db.client.get(path, headers=db.headers("C"))
                    for path in ["/health", "/sync/pull", "/social/conversations"]
                ]
            ),
            3,
        )
        assert [r.status_code for r in results] == [200, 200, 200]
        assert engine.pool.checkedout() == db.engine.pool.checkedout() == 0
        assert not pending.done()
    finally:
        released.set()
        try:
            await asyncio.wait_for(pending, 10)
        finally:
            stop()
    async with queue.factory() as session:
        job = await session.get(
            VisionJob, queue.job_id if flow == "reference" else queue.gpu_id
        )
        if flow == "reference":
            assert job.status == "completed"
            assert (await session.get(CatPhoto, queue.photo_id)).analysis == {
                "coat_type": "fixture"
            }
        else:
            assert job.status == "pending" and job.attempts == 1
    evidence(
        "DB05-pool-" + flow,
        {
            "scenario": "DB-05",
            "flow": flow,
            "pool_events_at_barrier": at_barrier,
            "held_connections": 0,
            "parallel_fixture_pool_separate": True,
            "parallel_http_statuses": [200, 200, 200],
            "recognition_error_injected_after_observation": flow == "recognition",
        },
    )
