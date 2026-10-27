"""Cold/warm local-model resource campaign; each role/trial has a new process."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest
from security_cases import evidence
from subprocesses import run_logged

pytest_plugins = ["current_offline_cases"]


@pytest.mark.asyncio
async def test_RSC01_actual_lifespan_boot_and_shutdown(own_api, monkeypatch):
    import httpx
    from sqlalchemy import func, select
    from measurements import resources
    from app import main
    from app.persistence_models import VisionJob

    monkeypatch.setattr(main, "get_session_factory", lambda: own_api.factory)
    before_tasks = set(asyncio.all_tasks())
    before = resources()
    started = time.perf_counter()
    async with main.lifespan(main.app):
        boot_seconds = time.perf_counter() - started
        async with httpx.AsyncClient(base_url=own_api.url, timeout=10) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/sync/pull", headers={
                "Authorization": "Bearer " + own_api.bundles[0]["access_token"],
            })).status_code == 200
        await asyncio.sleep(2)
        running = resources()
        async with own_api.factory() as session:
            assert await session.scalar(select(func.count()).select_from(VisionJob)) == 0
        created = set(asyncio.all_tasks()) - before_tasks
        assert len(created) >= 3, "Bridge, scheduler and reference worker must actually start"
    await asyncio.sleep(0)
    remaining = [task for task in created if not task.done()]
    assert not remaining and own_api.factory.kw["bind"].pool.checkedout() == 0
    assert own_api.provider_calls == []
    evidence("RSC01-lifespan", {
        "scenario": "RSC-01", "actual_lifespan_started": True,
        "boot_seconds": boot_seconds, "startup_tasks_observed": len(created),
        "health": 200, "authenticated_pull": 200, "background_jobs": 0,
        "startup_tasks_remaining_after_shutdown": 0, "pool_checked_out_after_shutdown": 0,
        "resources_before": before, "resources_running": running, "resources_after": resources(),
        "paid_calls": 0, "scope": "Real lifespan bridge/scheduler/reference consumer, own migrated PostgreSQL and TCP API; two seconds idle. No model batch or universal startup SLA claim.",
    })


@pytest.mark.asyncio
async def test_RSC01_fresh_api_and_worker_processes():
    root = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    root.mkdir(parents=True, exist_ok=True)
    cache = Path(os.environ["QUALITY_MODEL_CACHE"])
    weights = [cache / name for name in (
        "sam2.1_hiera_tiny.pt", "checkpoints/dinov2_vits14_pretrain.pth",
        "checkpoints/fasterrcnn_mobilenet_v3_large_320_fpn-907ea3f9.pth",
    )]
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in weights}
    results = []
    roles = json.loads(os.environ.get("QUALITY_LOCAL_MODEL_ROLES", '["api", "worker"]'))
    assert roles in (["api", "worker"], ["api"], ["worker"])
    for role in roles:
        for trial in range(2):
            folder = root / f"{role}-{trial}"
            folder.mkdir()
            env = dict(os.environ, QUALITY_CASE_EVIDENCE=str(folder))

            def execute():
                with (folder / "pytest.log").open("w", encoding="utf-8") as log:
                    return run_logged([
                        sys.executable, "-X", "utf8", "-m", "pytest", "-q", "-ra",
                        str(Path(__file__).with_name("local_model_probe_cases.py")) + f"::test_RSC01_real_local_models[{role}]",
                        "--confcutdir", str(Path(__file__).parent),
                        "--junitxml", str(folder / "junit.xml"),
                    ], cwd=os.environ["QUALITY_TARGET_SOURCE"], env=env, log=log, timeout=260)

            started = time.perf_counter()
            result = await asyncio.to_thread(execute)
            assert result.returncode == 0, f"{role}/{trial} failed; preserve and inspect {folder / 'pytest.log'}"
            observation = json.loads((folder / "RSC01-local-models.json").read_text())
            observation["trial"] = trial
            observation["process_wall_seconds_including_import_startup_and_teardown"] = time.perf_counter() - started
            results.append(observation)
            print(f"RSC01 {role}/{trial}: cold + three warm + idle passed", flush=True)
    assert len({row["pid"] for row in results}) == 2 * len(roles)
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[path.name] for path in weights)
    evidence("RSC01-fresh-processes", {
        "scenario": "RSC-01", "weights_sha256": hashes, "trials": results,
        "paid_calls": 0, "processes": len(results), "roles": roles,
        "sampling": "Two fresh process trials per role; one cold and three warm executions each; 5s idle at 10Hz. Bounded resource observations, no inferential percentile/speedup claims. OS file cache remains warm, including SHA preflight reads.",
    })
