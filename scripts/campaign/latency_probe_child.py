"""Separate HTTP generator for bounded LAT-08 probes; no product imports or device."""

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx
from measurements import resources


async def run(config):
    options = json.loads(Path(config).read_text())
    count = options.get("requests_per_route", 1000)
    warmup = options.get("warmup_per_route", 30)
    assert 1 <= count <= 1000 and 1 <= warmup <= 30
    if options.get("generator_affinity_mask"):
        from windows_limits import set_affinity
        set_affinity(options["generator_affinity_mask"])
    url = urlsplit(options["url"])
    assert url.hostname == "127.0.0.1" and url.port != 8000
    out = Path(options["output"])
    rows = []
    semaphore = asyncio.Semaphore(5)
    async with httpx.AsyncClient(
        base_url=options["url"],
        headers={"Authorization": "Bearer " + options["token"]},
        timeout=options.get("request_timeout_seconds", 10),
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=5,
            keepalive_expiry=options.get("client_keepalive_seconds", 5)),
    ) as client:

        async def request(kind, path, record):
            queued = time.perf_counter()
            async with semaphore:
                start = time.perf_counter()
                response = await client.get(path)
                elapsed = time.perf_counter() - start
                assert response.status_code == 200, (
                    f"{kind}: status {response.status_code}"
                )
                body = response.json()
                if kind == "history":
                    assert len(body) == 20
                if kind == "pull":
                    assert len(body["changes"]) == 5
                if record:
                    rows.append(
                        {
                            "kind": kind,
                            "http_ms": elapsed * 1000,
                            "generator_queue_ms": (start - queued) * 1000,
                            "status": response.status_code,
                            "bytes": len(response.content),
                        }
                    )

        paths = options["paths"]
        warmed = await asyncio.gather(
            *(
                request(kind, path, False)
                for _ in range(warmup)
                for kind, path in paths.items()
            ), return_exceptions=True,
        )
        warmup_errors = dict(Counter(type(r).__name__ for r in warmed if isinstance(r, BaseException)))
        (out / "warmup-result.json").write_text(json.dumps({"failed_request_errors": warmup_errors})+"\n")
        assert not warmup_errors, "Warmup failed; preserve warmup-result.json"
        (out / "warmed.json").write_text("{}\n")
        deadline = time.monotonic() + 60
        while not (out / "measure.json").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        before = resources()
        started = time.perf_counter()
        completed = await asyncio.gather(
            *(
                request(kind, path, True)
                for _ in range(count)
                for kind, path in paths.items()
            ), return_exceptions=True,
        )
        errors = dict(Counter(type(r).__name__ for r in completed if isinstance(r, BaseException)))
        result = {
            "warmup_per_route": warmup,
            "connection_configuration": {key: options.get(key) for key in (
                "request_timeout_seconds", "client_keepalive_seconds", "server_keepalive_seconds")},
            "samples_per_route": count,
            "concurrency": 5,
            "elapsed_seconds": time.perf_counter() - started,
            "resources_before": before,
            "resources_after": resources(),
            "rows": rows,
            "attempted": 3*count,
            "completed_successful": len(rows),
            "failed_request_errors": errors,
            "scope": "Client complete-body HTTP time excludes generator semaphore queue; generated fixture data only.",
        }
        (out / "generator-result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"LAT08 generator: {len(rows)} successful requests", flush=True)
        assert not errors, "Requests failed; preserve partial generator-result.json"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    a = p.parse_args()
    asyncio.run(run(a.config))
