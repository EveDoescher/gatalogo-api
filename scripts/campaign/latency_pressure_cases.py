"""Bounded real TCP latency/lag/pool observations with a separate load generator."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

import httpx
import pytest

pytest.register_assert_rewrite("current_offline_cases", "image_cases", "security_cases")
pytest_plugins = ["current_offline_cases"]
from security_cases import evidence
from image_cases import image_bytes
from measurements import resources
from subprocesses import run_logged
from app import main
from app.persistence_models import (
    User,
    HabitatWallet,
    RemoteCat,
    SyncChange,
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
)
from app.services.auth_service import issue_tokens


@pytest.mark.asyncio
async def test_LAT08_tcp_fixed_load_during_controlled_inference(
    own_api, monkeypatch, caplog
):
    count = int(os.environ.get("QUALITY_LATENCY_REQUESTS_PER_ROUTE", "1000"))
    warmup = int(os.environ.get("QUALITY_LATENCY_WARMUP_PER_ROUTE", "30"))
    assert 1 <= count <= 1000 and 1 <= warmup <= 30
    for logger in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error"):
        caplog.set_level(logging.WARNING, logger=logger)
    root = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "latency"
    root.mkdir(parents=True)
    engine = own_api.factory.kw["bind"]
    pool = engine.sync_engine.pool
    observed = {"active": False, "lags": [], "pool_wait_ms": [], "max_checkout": 0}
    get = pool._do_get

    def acquire():
        start = time.perf_counter()
        try:
            return get()
        finally:
            if observed["active"]:
                observed["pool_wait_ms"].append((time.perf_counter() - start) * 1000)

    monkeypatch.setattr(pool, "_do_get", acquire)

    async def monitor():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            if observed["active"]:
                observed["lags"].append(max(0, (now - previous - 0.01) * 1000))
                observed["max_checkout"] = max(
                    observed["max_checkout"], pool.checkedout()
                )
            previous = now

    watcher = asyncio.create_task(monitor())
    service = main.AnalysisService(None)
    original = service._analyze_with_gemini
    phases = []
    try:
        for phase, models in enumerate([0, 1, 5]):
            folder = root / f"models-{models}"
            folder.mkdir()
            ids = [uuid4() for _ in range(5)]
            async with own_api.factory() as db:
                owner = User(
                    id=uuid4(),
                    email=f"latency-{phase}@example.invalid",
                    status="active",
                )
                db.add(owner)
                await db.flush()
                db.add(HabitatWallet(user_id=owner.id, balance=100))
                bundle = await issue_tokens(db, user=owner, device_id="latency-fixture")
                stamp = datetime(2026, 10, 27, tzinfo=UTC)
                for i, ident in enumerate(ids):
                    cat = RemoteCat(
                        id=uuid4(),
                        user_id=owner.id,
                        client_id=ident,
                        payload={
                            "name": f"latency-{i}",
                            "status": "pending_analysis",
                            "individual_id": "pending",
                        },
                        client_updated_at=stamp,
                        updated_by_device="latency-fixture",
                    )
                    db.add(cat)
                    await db.flush()
                    db.add(SyncChange(user_id=owner.id, cat_id=cat.id))
                conversation = Conversation(id=uuid4())
                db.add(conversation)
                await db.flush()
                db.add_all(
                    [
                        ConversationMember(conversation_id=conversation.id, user_id=u)
                        for u in [owner.id, own_api.ids[1]]
                    ]
                )
                db.add_all(
                    [
                        ConversationMessage(
                            conversation_id=conversation.id,
                            sender_id=own_api.ids[1],
                            body=f"fixture-{i:02d}",
                            created_at=stamp + timedelta(seconds=i),
                        )
                        for i in range(20)
                    ]
                )
                db.add_all(
                    [
                        Friendship(user_id=a, friend_id=b)
                        for a, b in [
                            (owner.id, own_api.ids[1]),
                            (own_api.ids[1], owner.id),
                        ]
                    ]
                )
                await db.commit()
            entered, released = threading.Event(), threading.Event()
            lock = threading.Lock()
            state = {"entered": 0}

            def provider(*args):
                with lock:
                    state["entered"] += 1
                    if state["entered"] == models:
                        entered.set()
                if not released.wait(120):
                    raise TimeoutError(
                        "Controlled inference barrier exceeded 120 seconds"
                    )
                return original(*args)

            monkeypatch.setattr(service, "_analyze_with_gemini", provider)
            pending = []
            async with httpx.AsyncClient(
                base_url=own_api.url,
                headers={"Authorization": "Bearer " + bundle["access_token"]},
                timeout=150,
            ) as analysis:
                try:
                    for i in range(models):
                        pending.append(
                            asyncio.create_task(
                                analysis.post(
                                    "/analyze",
                                    data={"cat_id": str(ids[i])},
                                    files={
                                        "image": (
                                            "fixture.png",
                                            image_bytes(
                                                size=(256 + phase * 20 + i, 128)
                                            ),
                                            "image/png",
                                        )
                                    },
                                )
                            )
                        )
                    if models:
                        assert await asyncio.to_thread(entered.wait, 15)
                        assert not any(p.done() for p in pending)
                        assert pool.checkedout() == 0
                    config = {
                        "requests_per_route": count,
                        "warmup_per_route": warmup,
                        "generator_affinity_mask": int(os.environ.get("QUALITY_GENERATOR_AFFINITY_MASK", "0")),
                        "url": own_api.url,
                        "token": bundle["access_token"],
                        "output": str(folder),
                        "paths": {
                            "health": "/health",
                            "pull": "/sync/pull",
                            "history": f"/social/conversations/{conversation.id}/messages",
                        },
                    }
                    path = folder / "private-generator-config.json"
                    path.write_text(json.dumps(config) + "\n")

                    def generate():
                        with (folder / "generator.log").open(
                            "w", encoding="utf-8"
                        ) as log:
                            return run_logged(
                                [
                                    sys.executable,
                                    "-X",
                                    "utf8",
                                    str(
                                        Path(__file__).with_name(
                                            "latency_probe_child.py"
                                        )
                                    ),
                                    "--config",
                                    str(path),
                                ],
                                cwd=Path(os.environ["QUALITY_TARGET_SOURCE"]),
                                env=dict(os.environ),
                                log=log,
                                timeout=140,
                            )

                    task = asyncio.create_task(asyncio.to_thread(generate))
                    deadline = time.monotonic() + 45
                    while not (folder / "warmed.json").exists():
                        assert not task.done(), "Generator failed before calibration"
                        assert time.monotonic() < deadline
                        await asyncio.sleep(0.02)
                    observed.update(
                        active=True, lags=[], pool_wait_ms=[], max_checkout=0
                    )
                    before = resources()
                    started = time.perf_counter()
                    (folder / "measure.json").write_text("{}\n")
                    result = await task
                    elapsed = time.perf_counter() - started
                    after = resources()
                    observed["active"] = False
                    assert result.returncode == 0
                    assert all(not p.done() for p in pending), (
                        "Inference gate ended before light workload"
                    )
                    for _ in range(100):
                        if pool.checkedout() == 0:
                            break
                        await asyncio.sleep(0.01)
                    assert pool.checkedout() == 0
                    generated = json.loads(
                        (folder / "generator-result.json").read_text()
                    )
                    assert len(generated["rows"]) == 3 * count
                    samples = {
                        "models_waiting": models,
                        "http_samples": 3 * count,
                        "same_fixed_concurrency": 5,
                        "provider_wait_type": "thread event; I/O wait control, not real model CPU",
                        "server_elapsed_seconds": elapsed,
                        "server_resources_before": before,
                        "server_resources_after": after,
                        "loop_lag_ms": list(observed["lags"]),
                        "pool_acquire_ms_including_connection_creation": list(
                            observed["pool_wait_ms"]
                        ),
                        "pool_checkedout_peak_observed": observed["max_checkout"],
                        "pool_checkedout_before_release": pool.checkedout(),
                        "generator_process_separate": True,
                        "generator_resources": {
                            "before": generated["resources_before"],
                            "after": generated["resources_after"],
                        },
                    }
                    (folder / "server-result.json").write_text(
                        json.dumps(samples, indent=2) + "\n"
                    )
                    phases.append(samples)
                finally:
                    observed["active"] = False
                    released.set()
                    replies = await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True), 25
                    )
                    assert all(
                        isinstance(r, httpx.Response) and r.status_code == 200
                        for r in replies
                    ), "Controlled analyses must finish after gate release"
            print(f"LAT08 phase models={models}: {3 * count} requests passed", flush=True)
        assert len(own_api.provider_calls) == 6
        evidence(
            "LAT08-controlled-tcp",
            {
                "phases": phases,
                "passed_light_requests": 9 * count,
                "completed_controlled_analyses": 6,
                "paid_calls": 0,
                "warmup_per_route": warmup,
                "samples_per_route": count,
                "scope": "Real TCP/API/PostgreSQL. Generator is another OS process; server also hosts low-volume test coordination. Pool private acquisition hook is version-specific and includes connection creation. I/O-gated provider only; no real-model CPU, saturation or production-capacity claim.",
            },
        )
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
