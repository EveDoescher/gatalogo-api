"""Bounded lifecycle repetition in one process; no paid inference or soak load."""

import asyncio
from datetime import UTC, datetime, timedelta
import gc
import json
import logging
import os
from pathlib import Path
import threading
import time
import tracemalloc
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from websockets.asyncio.client import connect

pytest_plugins = ["current_upload_transport_cases"]
pytest.register_assert_rewrite("image_cases")
from image_cases import image_bytes
from measurements import resources
from network_cases import read_event, token
from security_cases import evidence
from app.persistence_models import Conversation, ConversationMember, Friendship
from app.services.websocket_manager import ws_manager


@pytest.mark.asyncio
async def test_RSC02_three_rounds_of_100_lifecycles_without_process_restart(
    live, own_api, caplog
):
    # Pytest otherwise retains every successful HTTP/WS INFO record for the
    # whole test, manufacturing heap growth unrelated to the application's use.
    for name in (
        "httpx",
        "httpcore",
        "uvicorn.error",
        "uvicorn.access",
        "app.services.websocket_manager",
    ):
        caplog.set_level(logging.WARNING, logger=name)
    chat, client_id = uuid4(), uuid4()
    async with live.db() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all(
            [ConversationMember(conversation_id=chat, user_id=u) for u in own_api.ids]
        )
        session.add_all(
            [
                Friendship(user_id=a, friend_id=b)
                for a, b in (own_api.ids, list(reversed(own_api.ids)))
            ]
        )
        await session.commit()
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    folder.mkdir(parents=True, exist_ok=True)
    images = [image_bytes(size=(128 + i, 128)) for i in range(2)]
    pool = own_api.factory.kw["bind"].sync_engine.pool
    base_time = datetime.now(UTC) - timedelta(minutes=5)
    path = f"/sync/cats/{client_id}/photo"
    cursor = 0
    started = time.perf_counter()

    def snapshot():
        return resources() | {
            "threads": threading.active_count(),
            "asyncio_tasks": len(asyncio.all_tasks()),
            "pool_checkedout": pool.checkedout(),
            "pool_checkedin": pool.checkedin(),
            "pool_connections": pool.checkedin() + pool.checkedout(),
            "http_connections": len(live.a._transport._pool.connections)
            + len(live.other._transport._pool.connections),
            "own_active_sockets": sum(
                len(ws_manager._connections.get(u, ())) for u in own_api.ids
            ),
            "python_traced_bytes": tracemalloc.get_traced_memory()[0],
        }

    async def cycle(index):
        nonlocal cursor
        at = base_time + timedelta(milliseconds=2 * index)
        pushed = await live.a.post(
            "/sync/push",
            json={
                "operations": [
                    {
                        "client_id": str(client_id),
                        "updated_at": at.isoformat(),
                        "device_id": "resource-fixture",
                        "payload": {
                            "status": "pending_analysis",
                            "individual_id": "pending",
                        },
                    }
                ]
            },
        )
        assert pushed.status_code == 200
        data = images[index % 2]
        fields = {
            "updated_at": (at + timedelta(milliseconds=1)).isoformat(),
            "device_id": "resource-fixture",
        }
        uploaded = await live.a.put(
            path, data=fields, files={"image": ("own.png", data, "image/png")}
        )
        assert uploaded.status_code == 204
        pulled = await live.a.get(f"/sync/pull?cursor={cursor}")
        assert pulled.status_code == 200
        body = pulled.json()
        assert len(body["changes"]) == 1 and body["changes"][0]["client_id"] == str(
            client_id
        )
        assert body["next_cursor"] > cursor
        cursor = body["next_cursor"]
        photo = await live.a.get(path)
        assert photo.status_code == 200 and photo.content == data
        denied = await live.other.put(
            path, data=fields, files={"image": ("own.png", data, "image/png")}
        )
        assert denied.status_code == 404
        async with connect(
            live.url.replace("http://", "ws://") + "/ws?token=" + token(live.other),
            proxy=None,
            open_timeout=5,
            close_timeout=2,
        ) as ws:
            message = await live.a.post(
                f"/social/conversations/{chat}/messages",
                json={"body": f"resource-cycle-{index}"},
            )
            assert message.status_code == 201
            received = await read_event(ws, "new_message")
            assert received["data"]["message"]["id"] == message.json()["id"]
            peak = snapshot()
        for _ in range(50):
            if not ws_manager.is_online(own_api.ids[1]) and pool.checkedout() == 0:
                break
            await asyncio.sleep(0.01)
        assert not ws_manager.is_online(own_api.ids[1]) and pool.checkedout() == 0
        return peak

    tracemalloc.start(1)
    rounds, warm, deviations = [], [], []
    try:
        # Ten alternating-image cycles exercise every lifecycle path five times
        # before the baseline; this is pool/cache warmup, not statistical sampling.
        with (folder / "RSC02-cycles.jsonl").open("w", encoding="utf-8") as log:
            for index in range(10):
                warm.append(await cycle(index))
            gc.collect()
            await asyncio.sleep(0.1)
            baseline = snapshot()
            memory_before = tracemalloc.take_snapshot()
            memory_before.dump(str(folder / "RSC02-baseline.tracemalloc"))
            gc.collect()
            baseline = snapshot()  # Include the profiler's retained snapshot.
            envelope = {
                k: max(s[k] for s in warm)
                for k in (
                    "handles",
                    "threads",
                    "asyncio_tasks",
                    "http_connections",
                    "pool_connections",
                )
            }
            # Idle connections may legitimately warm up after calibration;
            # use configured idle capacity and require zero checked-out sessions.
            envelope["pool_connections"] = pool.size()
            for round_index in range(3):
                begin = snapshot()
                peak = dict(begin)
                total, low, high = 0.0, float("inf"), 0.0
                for offset in range(100):
                    index = 10 + round_index * 100 + offset
                    tick = time.perf_counter()
                    sample = await cycle(index)
                    elapsed = (time.perf_counter() - tick) * 1000
                    total += elapsed
                    low, high = min(low, elapsed), max(high, elapsed)
                    for key in (
                        "rss_bytes",
                        "handles",
                        "threads",
                        "asyncio_tasks",
                        "http_connections",
                        "pool_checkedout",
                    ):
                        peak[key] = max(peak[key], sample[key])
                    log.write(
                        json.dumps(
                            {
                                "round": round_index + 1,
                                "cycle": offset + 1,
                                "elapsed_ms": elapsed,
                                "resources_observed": sample,
                            }
                        )
                        + "\n"
                    )
                log.flush()
                gc.collect()
                await asyncio.sleep(0.2)
                rest = snapshot()
                rounds.append(
                    {
                        "round": round_index + 1,
                        "cycles": 100,
                        "before": begin,
                        "observed_peak": peak,
                        "rest_after_gc": rest,
                        "cycle_ms_observed": {
                            "min": low,
                            "mean": total / 100,
                            "max": high,
                        },
                    }
                )
                (folder / "RSC02-rounds-progress.json").write_text(
                    json.dumps(
                        {
                            "baseline": baseline,
                            "warm_envelope": envelope,
                            "rounds": rounds,
                            "deviations": deviations,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                for key in ("own_active_sockets", "pool_checkedout"):
                    if rest[key] != 0:
                        deviations.append(
                            {
                                "round": round_index + 1,
                                "metric": key,
                                "actual": rest[key],
                                "limit": 0,
                            }
                        )
                for key in (
                    "handles",
                    "threads",
                    "asyncio_tasks",
                    "http_connections",
                    "pool_connections",
                ):
                    if rest[key] > envelope[key]:
                        deviations.append(
                            {
                                "round": round_index + 1,
                                "metric": key,
                                "actual": rest[key],
                                "limit": envelope[key],
                            }
                        )
            memory_after = tracemalloc.take_snapshot()
            memory_after.dump(str(folder / "RSC02-final.tracemalloc"))
            changes = [
                {
                    "file": str(s.traceback[0].filename).replace(
                        str(Path(os.environ["QUALITY_TARGET_SOURCE"])), "<candidate>"
                    ),
                    "line": s.traceback[0].lineno,
                    "size_diff": s.size_diff,
                    "count_diff": s.count_diff,
                }
                for s in memory_after.compare_to(memory_before, "lineno")[:20]
            ]
        assert own_api.provider_calls == []
        history = await live.other.get(f"/social/conversations/{chat}/messages")
        assert history.status_code == 200 and len(history.json()) == 310
        evidence(
            "RSC02-lifecycle",
            {
                "warmup_cycles": 10,
                "rounds": rounds,
                "same_process": True,
                "baseline": baseline,
                "warm_observed_envelope": envelope,
                "tracemalloc_top_differences": changes,
                "elapsed_seconds": time.perf_counter() - started,
                "committed_messages": 310,
                "paid_calls": 0,
                "resource_deviations": deviations,
                "pool_idle_capacity": pool.size(),
                "keepalive_seconds": 5,
                "tracemalloc_frames": 1,
                "scope": "API/test Python process, own PostgreSQL/HTTP/WS/storage. RSS/heap descriptive, not proof that all leaks are absent. Rest GC applied consistently; successful request logs suppressed in test capture to avoid observer retention. Exact pool/socket closure and rest handles/threads/tasks/HTTP bounded by measured warm lifecycle envelope.",
            },
        )
        assert not deviations, (
            "Inspect all three recorded rounds for resource deviations"
        )
    finally:
        if not (folder / "RSC02-final.tracemalloc").exists():
            tracemalloc.take_snapshot().dump(
                str(folder / "RSC02-interrupted.tracemalloc")
            )
        tracemalloc.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", ["persistent", "close"])
async def test_RSC02_diagnostic_denied_multipart_keepalive(
    live, own_api, connection, caplog
):
    for name in ("httpx", "uvicorn.access"):
        caplog.set_level(logging.WARNING, logger=name)
    ident = str(uuid4())
    at = datetime.now(UTC).isoformat()
    created = await live.a.post(
        "/sync/push",
        json={
            "operations": [
                {
                    "client_id": ident,
                    "updated_at": at,
                    "device_id": "diagnostic",
                    "payload": {
                        "individual_id": "pending",
                        "status": "pending_analysis",
                    },
                }
            ]
        },
    )
    assert created.status_code == 200
    path = f"/sync/cats/{ident}/photo"
    result = {
        "connection": connection,
        "completed": 0,
        "slow_requests": [],
        "failure": None,
    }
    monitor = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    try:
        for index in range(200):
            started = time.perf_counter()
            call = asyncio.create_task(
                live.other.put(
                    path,
                    data={"updated_at": at, "device_id": "diagnostic"},
                    files={
                        "image": ("own.png", image_bytes(size=(128, 128)), "image/png")
                    },
                    headers={"Connection": "close"} if connection == "close" else {},
                )
            )
            done, _ = await asyncio.wait([call], timeout=1)
            if not done:
                stacks = []
                for task in asyncio.all_tasks():
                    current, chain = task.get_coro(), []
                    for _ in range(25):
                        code, frame = (
                            getattr(current, "cr_code", None),
                            getattr(current, "cr_frame", None),
                        )
                        if code is not None:
                            chain.append(
                                {
                                    "file": code.co_filename,
                                    "function": code.co_name,
                                    "line": frame.f_lineno if frame else None,
                                }
                            )
                        current = getattr(current, "cr_await", None)
                        if current is None:
                            break
                    stacks.append(chain)
                async with monitor.connect() as db:
                    activity = [
                        dict(r)
                        for r in (
                            await db.execute(
                                text(
                                    "SELECT pid,state,wait_event_type,wait_event,query,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE datname=current_database()"
                                )
                            )
                        ).mappings()
                    ]
                result["slow_requests"].append(
                    {
                        "iteration": index,
                        "pool_status": own_api.factory.kw[
                            "bind"
                        ].sync_engine.pool.status(),
                        "activity": activity,
                        "stacks": stacks,
                    }
                )
            try:
                reply = await call
                assert reply.status_code == 404
                result["completed"] += 1
            except Exception as error:
                result["failure"] = {
                    "iteration": index,
                    "error_type": type(error).__name__,
                    "elapsed_seconds": time.perf_counter() - started,
                }
                break
    finally:
        await monitor.dispose()
        evidence("RSC02-denied-diagnostic-" + connection, result)
    assert result["failure"] is None and result["completed"] == 200
