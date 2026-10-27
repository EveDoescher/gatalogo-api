"""Bounded-rate/closed-loop HTTP generator; never imports product code."""

import argparse
import asyncio
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from measurements import resources


async def run(path):
    cfg = json.loads(Path(path).read_text())
    url = urlsplit(cfg["url"])
    assert url.hostname == "127.0.0.1" and url.port not in (80, 8000)
    out = Path(cfg["output"])
    actors = cfg["actors"]
    stats, errors, auth = Counter(), Counter(), Counter()
    locks = [asyncio.Lock() for _ in actors]
    samples = defaultdict(list)
    queue_samples = defaultdict(list)
    measured = False
    sequence = 0
    offered = 0
    pending_peak = 0
    routes = [("pull", "/sync/pull"), ("inbox", "/social/conversations"),
        ("feed", "/social/feed?limit=20&mode=all"), ("yard", "/app/quintal")]
    mix = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3]
    csv_file = (out / "requests.csv").open("w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    writer.writerow(["seq", "actor", "kind", "offered_at", "http_ms", "generator_queue_ms", "status", "bytes", "valid"])
    semaphore = asyncio.Semaphore(100)

    async with httpx.AsyncClient(base_url=cfg["url"], timeout=15,
        limits=httpx.Limits(max_connections=110, max_keepalive_connections=60,
            keepalive_expiry=cfg.get("keepalive_expiry_seconds", 5))) as client:
        async def token(index):
            actor = actors[index]
            async with locks[index]:
                if actor["expires_at"] - time.time() < 60:
                    reply = await client.post("/auth/refresh", json={"refresh_token": actor["refresh_token"], "device_id": "long-load-fixture"})
                    auth[str(reply.status_code)] += 1
                    assert reply.status_code == 200
                    bundle = reply.json()
                    actor.update(access_token=bundle["access_token"], refresh_token=bundle["refresh_token"], expires_at=time.time()+bundle["expires_in"])
                return actor["access_token"]

        async def request(index, route_index, offered_at, seq):
            kind, endpoint = routes[route_index]
            status, size, valid = 0, 0, False
            started = None
            try:
                async with semaphore:
                    started = time.perf_counter()
                    response = await client.get(endpoint, headers={"Authorization": "Bearer " + await token(index)})
                    status, size = response.status_code, len(response.content)
                    if status == 200:
                        body = response.json()
                        actor = actors[index]
                        if kind == "pull":
                            valid = isinstance(body, dict) and {x["client_id"] for x in body["changes"]} == set(actor["cat_ids"])
                        elif kind == "inbox":
                            valid = isinstance(body, list) and [x["id"] for x in body] == [actor["conversation_id"]]
                        elif kind == "feed":
                            valid = isinstance(body, list) and [x["id"] for x in body] == actor["feed_ids"]
                        else:
                            valid = isinstance(body, dict) and set(actor["yard_keys"]).issubset(body)
            except asyncio.CancelledError:
                errors["cancelled_drain_deadline"] += int(measured)
                raise
            except Exception as error:
                if measured:
                    errors[type(error).__name__] += 1
            finally:
                if measured:
                    now = time.perf_counter()
                    duration = (now - (started or offered_at)) * 1000
                    queue = ((started or now) - offered_at) * 1000
                    stats[(kind, str(status), str(valid))] += 1
                    samples[kind].append(duration)
                    queue_samples[kind].append(queue)
                    writer.writerow([seq, index, kind, offered_at, duration, queue, status, size, valid])

        async def drive(duration, rate):
            nonlocal sequence, offered, pending_peak
            deadline = time.perf_counter() + duration
            if cfg["mode"] == "closed":
                async def worker(index):
                    nonlocal sequence, offered
                    step = 0
                    while time.perf_counter() < deadline:
                        sequence += 1
                        offered += int(measured)
                        await request(index, step % 4, time.perf_counter(), sequence)
                        step += 1
                await asyncio.gather(*(worker(i) for i in range(cfg["concurrency"])))
                return
            pending = set()
            number = round(duration * rate)
            start = time.perf_counter()
            for step in range(number):
                due = start + step / rate
                await asyncio.sleep(max(0, due - time.perf_counter()))
                sequence += 1
                offered += int(measured)
                if len(pending) >= 500:
                    errors["shed_safety_backlog"] += int(measured)
                    continue
                task = asyncio.create_task(request(step % len(actors), mix[step % 10], due, sequence))
                pending.add(task)
                task.add_done_callback(pending.discard)
                pending_peak = max(pending_peak, len(pending))
            await asyncio.sleep(max(0, deadline - time.perf_counter()))
            if pending:
                _, left = await asyncio.wait(pending, timeout=30)
                for task in left:
                    task.cancel()
                await asyncio.gather(*left, return_exceptions=True)

        await drive(cfg["warmup_seconds"], cfg.get("warmup_rate", cfg.get("rate", 1)))
        (out / "warmed.json").write_text("{}\n")
        until = time.monotonic() + 60
        while not (out / "measure.json").exists():
            assert time.monotonic() < until
            await asyncio.sleep(0.02)
        before = resources()
        started = time.perf_counter()
        measured = True
        await drive(cfg["duration_seconds"], cfg.get("rate", 1))
        elapsed = time.perf_counter() - started
        csv_file.close()
        result = {"mode": cfg["mode"], "concurrency": cfg.get("concurrency"), "rate": cfg.get("rate"),
            "duration_seconds": cfg["duration_seconds"], "warmup_seconds": cfg["warmup_seconds"],
            "elapsed_including_drain_seconds": elapsed, "offered": offered,
            "completed": sum(stats.values()), "offered_rps": offered / cfg["duration_seconds"],
            "completed_rps_including_drain": sum(stats.values()) / elapsed,
            "responses": [{"kind": kind, "status": status, "valid": valid == "True", "count": n}
                for (kind, status, valid), n in stats.items()],
            "errors": dict(errors), "refresh_statuses": dict(auth), "pending_peak": pending_peak,
            "latencies_ms": dict(samples), "generator_queue_ms": dict(queue_samples),
            "resources_before": before, "resources_after": resources(),
            "scope": "Local exploratory load; raw client times and generator delay separated; no causal improvement or production-capacity claim"}
        (out / "private-accounts.json").write_text(json.dumps(actors))
        (out / "result.json").write_text(json.dumps(result))
        print(json.dumps({k: result[k] for k in ("mode", "offered", "completed", "elapsed_including_drain_seconds", "errors")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    asyncio.run(run(parser.parse_args().config))
