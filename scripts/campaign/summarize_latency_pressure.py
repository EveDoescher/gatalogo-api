"""Summarize LAT-08 using the campaign's existing percentile/uncertainty helpers."""

import argparse
from importlib import metadata
import json
from pathlib import Path

from measurements import summarize
from statistics_checks import quantile_interval


def analyze(run, output):
    run, output = Path(run), Path(output)
    if output.exists():
        raise ValueError("Never overwrite evidence")
    phases = []
    for models in (0, 1, 5):
        folder = run / "observations" / "latency" / f"models-{models}"
        generated = json.loads((folder / "generator-result.json").read_text())
        server = json.loads((folder / "server-result.json").read_text())
        assert (
            generated["resources_before"]["pid"]
            != server["server_resources_before"]["pid"]
        )
        assert server["pool_checkedout_before_release"] == 0
        routes = {}
        for kind in ("health", "pull", "history"):
            rows = [r for r in generated["rows"] if r["kind"] == kind]
            assert len(rows) == 1000 and all(r["status"] == 200 for r in rows)
            values = [r["http_ms"] for r in rows]
            routes[kind] = {
                **summarize([{"elapsed_ms": value} for value in values]),
                "p95_iid_conditional_interval": quantile_interval(values),
                "response_bytes": sum(r["bytes"] for r in rows),
                "generator_queue_excluded": True,
            }
        phases.append(
            {
                "models_waiting": models,
                "routes": routes,
                "requests": 3000,
                "fixed_client_concurrency": 5,
                "elapsed_seconds": generated["elapsed_seconds"],
                "observed_requests_per_second_not_capacity": 3000
                / generated["elapsed_seconds"],
                "event_loop_lag_ms": summarize(
                    [{"elapsed_ms": value} for value in server["loop_lag_ms"]]
                ),
                "pool_acquire_including_creation_ms": summarize(
                    [
                        {"elapsed_ms": value}
                        for value in server[
                            "pool_acquire_ms_including_connection_creation"
                        ]
                    ]
                ),
                "pool_checkedout_peak_observed": server[
                    "pool_checkedout_peak_observed"
                ],
                "pool_checkedout_before_release": server[
                    "pool_checkedout_before_release"
                ],
                "server_resources_before": server["server_resources_before"],
                "server_resources_after": server["server_resources_after"],
                "generator_resources": server["generator_resources"],
                "provider_wait_type": server["provider_wait_type"],
            }
        )
    result = {
        "phases": phases,
        "requests": 9000,
        "warmup_requests": 270,
        "sqlalchemy_version": metadata.version("SQLAlchemy"),
        "method": "Existing nearest-rank summary and binomial order-statistic P95 intervals; no new percentile implementation.",
        "limitations": [
            "Intervals are conditional on iid continuous observations; serial closed-loop dependence and host stationarity were not certified.",
            "Phases are not randomized independent paired blocks; some report generation also occurred on the host. Differences cannot be attributed solely to waiting analyses.",
            "Generator queue time is excluded from HTTP time. Generator has a separate PID; server also hosts low-volume coordination and monitoring.",
            "The pool acquisition hook is SQLAlchemy-version-specific and includes creation; 30 requests per route warm up each phase before measurements.",
            "Only I/O-gated synthetic inference is modeled. Real model CPU/GPU, SDK timeout behavior, capacity and production settings are not certified.",
            "Light HTTP timeout remains 10 s. Analysis client allows 150 s only to support the deliberately held <=120 s test barrier.",
            "No P99, optimization gain or universal responsiveness claim.",
        ],
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        "LAT-08: 9000 measured requests, 270 warmups, three independent generator processes"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    analyze(args.run, args.output)
