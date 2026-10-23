"""Real PG fences and worker process crash, no model/provider execution."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from sqlalchemy import text

SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"]).resolve()
assert SOURCE.is_relative_to(RUN / "sources")
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "tests"))
pytest.register_assert_rewrite("test_reference_analysis", "security_cases")
pytest_plugins = ["test_reference_analysis"]
from security_cases import evidence
from app.persistence_models import CatPhoto, RemoteCat, VisionJob
from app.services import reference_analysis as worker
from subprocesses import run_logged


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "photo-soft-delete",
        "cat-soft-delete",
        "photo-replace",
        "photo-hard-delete",
        "cat-hard-delete",
    ],
)
async def test_DB06_DATA06_result_after_resource_mutation(queue, change):
    claim = await worker._claim(queue.factory, queue.settings)
    async with queue.factory() as session:
        photo = await session.get(CatPhoto, queue.photo_id)
        cat = await session.get(RemoteCat, queue.cat_id)
        if change == "photo-soft-delete":
            photo.deleted_at = datetime.now(UTC)
        elif change == "cat-soft-delete":
            cat.deleted_at = datetime.now(UTC)
        elif change == "photo-replace":
            photo.content_hash = "f" * 64
        elif change == "photo-hard-delete":
            await session.delete(photo)
        else:
            await session.delete(cat)
        await session.commit()
    await worker._finish(
        queue.factory, claim, queue.settings, incoming={"forbidden": "stale-result"}
    )
    async with queue.factory() as session:
        job = await session.get(VisionJob, queue.job_id)
        photo = await session.get(CatPhoto, queue.photo_id)
        assert job is None or job.status == "superseded"
        assert photo is None or photo.analysis is None
    worker.confirm_photo.assert_not_awaited()
    worker.rebuild_analysis_from_photos.assert_not_awaited()
    evidence(
        "DB06-late-" + change,
        {
            "scenario": "DB-06",
            "variant": change,
            "backend": queue.backend,
            "stale_analysis_not_committed": True,
            "progression_not_called": True,
        },
    )


@pytest.mark.asyncio
async def test_IMG07_bytes_changed_between_claim_and_read_then_retry(queue):
    claim = await worker._claim(queue.factory, queue.settings)
    path = worker.photo_storage.open(claim.storage_key)
    original = path.read_bytes()
    path.write_bytes(b"controlled changed bytes")
    with pytest.raises(ValueError) as changed:
        worker._read_image(claim, queue.settings)
    await worker._finish(queue.factory, claim, queue.settings, error=changed.value)
    path.write_bytes(original)
    calls = []

    async def fake(**kwargs):
        calls.append(1)
        return {"source": "restored-file"}

    assert await worker.process_next(
        queue.factory, settings=queue.settings, analyzer=fake
    )
    async with queue.factory() as session:
        job = await session.get(VisionJob, queue.job_id)
        photo = await session.get(CatPhoto, queue.photo_id)
        assert job.status == "completed" and photo.analysis == {
            "source": "restored-file"
        }
    assert len(calls) == 1
    evidence(
        "IMG07-claim-read-change",
        {
            "scenario": "IMG-07",
            "changed_content_detected_before_inference": True,
            "inference_calls_after_restore": 1,
            "retry": "completed",
            "backend": queue.backend,
        },
    )


@pytest.mark.asyncio
async def test_RES04_actual_worker_process_killed_after_claim_then_restarted(
    queue, tmp_path
):
    async with queue.factory() as session:
        schema = await session.scalar(text("SELECT current_schema()"))
    assert schema.startswith("reference_")
    photo = worker.photo_storage.open("photo.jpg")
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "worker-process"
    folder.mkdir(parents=True, exist_ok=False)
    ready = folder / "claim-ready.json"
    env = dict(
        os.environ,
        QUALITY_WORKER_SCHEMA=schema,
        QUALITY_WORKER_PHOTO_PATH=str(photo),
        QUALITY_WORKER_READY=str(ready),
        QUALITY_WORKER_MODE="claim",
    )
    child = Path(os.environ["QUALITY_CAMPAIGN_ATTEMPT"]) / "executors/worker_child.py"
    with (folder / "claim.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(child)],
            cwd=SOURCE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert process.poll() is None, "Own worker exited before claim receipt"
                assert time.monotonic() < deadline, "Own worker claim deadline exceeded"
                await asyncio.sleep(0.1)
            claimed = json.loads(ready.read_text(encoding="utf-8"))
            assert claimed["attempt"] == 1
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    process.kill()
            process.wait(timeout=10)
    async with queue.factory() as session:
        job = await session.get(VisionJob, queue.job_id)
        assert job.status == "running" and job.attempts == 1
        job.started_at = datetime.now(UTC) - timedelta(seconds=31)
        await session.commit()
    env["QUALITY_WORKER_MODE"] = "finish"
    with (folder / "restart.log").open("w", encoding="utf-8") as log:
        result = await asyncio.to_thread(
            run_logged,
            [sys.executable, str(child)],
            cwd=SOURCE,
            env=env,
            log=log,
            timeout=40,
        )
    assert result.returncode == 0
    async with queue.factory() as session:
        job = await session.get(VisionJob, queue.job_id)
        photo = await session.get(CatPhoto, queue.photo_id)
        assert (
            job.status == "completed"
            and job.attempts == 2
            and photo.analysis == {"source": "restarted-worker-process"}
        )
    evidence(
        "RES04-real-process-crash",
        {
            "scenario": "RES-04",
            "first_claim_committed_before_os_kill": True,
            "lease_expiry": "controlled persisted started_at",
            "new_os_process_completed_attempt": 2,
            "stale_lease_recovered": True,
            "scope": "own worker subprocess, real PG, fake inference/progression; late old result is covered by separate fence tests",
        },
    )
