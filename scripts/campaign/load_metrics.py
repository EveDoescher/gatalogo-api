"""Descriptive completed-load metrics; requests are not independent repetitions."""

from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"n": 0}
    def quantile(q):
        position = (len(ordered)-1)*q
        low = int(position)
        high = min(low+1, len(ordered)-1)
        return ordered[low]+(ordered[high]-ordered[low])*(position-low)
    return {"n": len(ordered), "mean_ms": statistics.fmean(ordered),
        "median_ms": quantile(.5), "q25_ms": quantile(.25), "q75_ms": quantile(.75),
        "empirical_p95_ms": quantile(.95), "expected_observations_above_p95": .05*len(ordered),
        "min_ms": ordered[0], "max_ms": ordered[-1],
        "sample_stddev_ms": statistics.stdev(ordered) if len(ordered) > 1 else None}


def summarize(folder, phases):
    reports = []
    for phase in phases:
        path = Path(folder) / "observations" / phase["phase"] / "requests.csv"
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        assert len(rows) == phase["completed"]
        successful = [r for r in rows if r["status"] == "200" and r["valid"] == "True"]
        failed = [r for r in rows if r["status"] != "200" or r["valid"] != "True"]
        # Five-minute offered-time blocks describe temporal variability, not CIs.
        blocks = defaultdict(list)
        first = min(float(r["offered_at"]) for r in rows)
        for row in rows:
            blocks[int((float(row["offered_at"])-first)//300)].append(row)
        reports.append({"phase": phase["phase"], "offered": phase["offered"],
            "completed_attempts": len(rows), "successful_valid_responses": len(successful),
            "failed_attempts": len(failed), "not_started_safety_shed": phase["errors"].get("shed_safety_backlog", 0),
            "successful_rps_including_drain": len(successful)/phase["elapsed_including_drain_seconds"],
            "successful_operation_ms": {kind: distribution([float(r["http_ms"]) for r in successful if r["kind"] == kind])
                for kind in sorted({r["kind"] for r in rows})},
            "failed_operation_ms": distribution([float(r["http_ms"]) for r in failed]),
            "generator_queue_ms": distribution([float(r["generator_queue_ms"]) for r in rows]),
            "offered_time_blocks_300s": [{"block": index, "completed": len(part),
                "failures": sum(r["status"] != "200" or r["valid"] != "True" for r in part),
                "operation_ms": distribution([float(r["http_ms"]) for r in part if r["status"] == "200" and r["valid"] == "True"])}
                for index, part in sorted(blocks.items())]})
    resources_path = Path(folder) / "observations/server-resources.jsonl"
    resources = [json.loads(line) for line in resources_path.read_text().splitlines() if line]
    return {"phases": reports,
        "api_and_test_coordinator_resources": {"samples": len(resources),
            "peak_rss_bytes": max(r["rss_bytes"] for r in resources),
            "peak_private_commit_bytes": max(r["private_commit_bytes"] for r in resources),
            "first": resources[0], "last": resources[-1]},
        "method": "Linear empirical quantiles, sample SD and IQR. Successful and failed operations separated. Operation time includes token refresh and response validation; queue delay separate. Five-minute buckets describe temporal variation; last buckets may be partial. No population CI/P99 or causal branch difference: one local time series, dependent requests, shared host. Resources include the in-process API and pytest observer, not database CPU/RAM. RSS high-water/retention does not alone prove a memory leak."}
