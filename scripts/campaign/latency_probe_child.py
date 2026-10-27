"""Separate HTTP generator for bounded LAT-08 probes; no product imports or device."""

import argparse
import asyncio
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx
from measurements import resources


async def run(config):
    options = json.loads(Path(config).read_text())
    url = urlsplit(options["url"])
    assert url.hostname == "127.0.0.1" and url.port != 8000
    out = Path(options["output"])
    rows = []
    semaphore = asyncio.Semaphore(5)
    async with httpx.AsyncClient(
        base_url=options["url"],
        headers={"Authorization": "Bearer " + options["token"]},
        timeout=10,
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
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
        await asyncio.gather(
            *(
                request(kind, path, False)
                for _ in range(30)
                for kind, path in paths.items()
            )
        )
        (out / "warmed.json").write_text("{}\n")
        deadline = time.monotonic() + 60
        while not (out / "measure.json").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        before = resources()
        started = time.perf_counter()
        await asyncio.gather(
            *(
                request(kind, path, True)
                for _ in range(1000)
                for kind, path in paths.items()
            )
        )
        result = {
            "warmup_per_route": 30,
            "samples_per_route": 1000,
            "concurrency": 5,
            "elapsed_seconds": time.perf_counter() - started,
            "resources_before": before,
            "resources_after": resources(),
            "rows": rows,
            "scope": "Client complete-body HTTP time excludes generator semaphore queue; generated fixture data only.",
        }
        (out / "generator-result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("LAT08 generator: 3000 successful requests", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    a = p.parse_args()
    asyncio.run(run(a.config))
