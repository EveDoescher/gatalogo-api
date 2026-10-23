"""Fast endpoint component probe; bounded, source-isolated and never a phone claim."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time

from api_probe import prohibit_external_network
from measurements import resources
from statistics_checks import quantile, quantile_interval, stationarity


async def run(source, output, budget):
    source = Path(source).resolve()
    output = Path(output).resolve()
    campaign = Path(os.environ["QUALITY_CAMPAIGN_RUN"]).resolve()
    assert source.is_relative_to(campaign / "sources") and output.is_relative_to(
        campaign
    )
    assert not output.exists()
    sys.path.insert(0, str(source))
    os.environ.update(
        GEMINI_API_KEY="", SMTP_ENABLED="false", FCM_SERVICE_ACCOUNT_FILE=""
    )
    prohibit_external_network()
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    logging.disable(logging.INFO)
    rows = []
    warm = []
    state = "BUDGET_EXHAUSTED"
    body = None
    started = time.perf_counter()
    before = resources()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://bounded-health"
    ) as client:
        for _ in range(6):
            window = []
            for _ in range(20):
                t = time.perf_counter()
                response = await client.get("/health")
                window.append((time.perf_counter() - t) * 1000)
                assert response.status_code == 200 and response.json()["status"] == "ok"
            warm.append(window)
            if len(warm) >= 3 and stationarity(warm, 0.1)["stable"]:
                break
        stable = stationarity(warm, 0.1)
        if not stable["stable"]:
            state = "WARMUP_UNSTABLE"
        else:
            for _ in range(1000):
                if time.perf_counter() - started > budget:
                    break
                t = time.perf_counter()
                response = await client.get("/health")
                elapsed = (time.perf_counter() - t) * 1000
                assert response.status_code == 200 and response.json()["status"] == "ok"
                payload = response.json()
                if body is None:
                    body = payload
                assert payload == body
                rows.append(elapsed)
            if len(rows) == 1000:
                state = "COLLECTED_EXPLORATORY"
    report = {
        "state": state,
        "transport": "ASGI",
        "endpoint": "GET /health",
        "samples_ms": rows,
        "warmup_windows_ms": warm,
        "warmup_guard": stable,
        "resources_before": before,
        "resources_after": resources(),
        "elapsed_seconds": time.perf_counter() - started,
        "response_contract": body,
        "response_sha256": hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()
        if body
        else None,
        "p99": None,
        "confirmed_gain": False,
        "limitations": [
            "In-process endpoint, no HTTP server/socket/mobile timing",
            "No DB/SQL work for health; not a substitute for catalog/inference workloads",
            "Conditional iid interval does not certify independence or host stability",
        ],
    }
    if rows:
        report.update(
            p50_ms=quantile(rows, 0.5),
            p95_ms=quantile(rows, 0.95),
            p95_iid_conditional_interval=quantile_interval(rows, 0.95),
        )
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--budget", type=float, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.source, args.output, args.budget))
