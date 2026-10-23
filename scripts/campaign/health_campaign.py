"""Preregister and collect a bounded exploratory fast-endpoint A/B comparison."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from preflight import observe
from statistics_checks import paired_block_interval
from subprocesses import run_logged


def save(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def windows_metadata():
    result = {}
    commands = {
        "power_scheme": ["powercfg", "/GETACTIVESCHEME"],
        "disk_io": [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk | Where-Object Name -eq '_Total' | Select-Object DiskBytesPersec,PercentDiskTime | ConvertTo-Json -Compress",
        ],
        "thermal_zone": [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | Select-Object CurrentTemperature | ConvertTo-Json -Compress",
        ],
    }
    for key, command in commands.items():
        try:
            value = subprocess.run(
                command, capture_output=True, text=True, errors="replace", timeout=8
            )
            result[key] = {
                "exit_code": value.returncode,
                "observed": value.stdout.strip()[:2000]
                if value.returncode == 0
                else None,
                "available": value.returncode == 0,
            }
        except (OSError, subprocess.TimeoutExpired):
            result[key] = {"available": False}
    result["qualification"] = (
        "ACPI zones and global IO are observations, not CPU throttling or per-core independence certification"
    )
    return result


def run(campaign, attempt):
    campaign = Path(campaign).resolve()
    stage = campaign / "attempts" / attempt
    output = stage / "bounded_health"
    output.mkdir(exist_ok=False)
    manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
    order = ["A", "B", "B", "A"] * 3
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "sources": {k: v["sha"] for k, v in manifest["sources"].items()},
        "transport": "ASGI GET /health only",
        "order": order,
        "blocks": 3,
        "requests_per_cell": 1000,
        "sizing_basis": "Initial fast-endpoint budget gives about 50 observations above P95 per complete cell; interval precision and dependence still require review. Three blocks are exploratory with sparse bootstrap support, never a definitive effect estimate.",
        "warmup": "20 requests/window, at most6 windows; require last3 median relative range <=10%, a predeclared diagnostic rather than proof of stationarity",
        "collection_wall_budget_seconds": 120,
        "per_cell_wall_budget_seconds": 15,
        "memory_safety_floor_bytes": 1024**3,
        "memory_basis": "at least ~5 times the observed ~165MiB API probe working set available before one sequential child starts",
        "instrumentation": "same installed dependencies, no lifespan, INFO logs disabled in both arms, no network, SQL or paid model",
        "p99": "DISABLED",
        "confirmatory": False,
        "reason": "No exclusive host or CPU-throttling/independent-block qualification",
    }
    save(output / "protocol.json", protocol)
    observe(
        output / "host_before.json", 20, 0.25, "before bounded endpoint observations"
    )
    save(output / "platform_before.json", windows_metadata())
    host = json.loads((output / "host_before.json").read_text(encoding="utf-8"))
    if min(s["available_physical_bytes"] for s in host["samples"]) < 1024**3:
        save(
            output / "summary.json",
            {
                "state": "ABORTED_MEMORY_SAFETY_GATE",
                "requests_executed": 0,
                "confirmatory": False,
            },
        )
        return 0
    started = time.perf_counter()
    cells = []
    for index, arm in enumerate(order):
        remaining = 120 - (time.perf_counter() - started)
        if remaining < 10:
            break
        folder = output / f"{index + 1:02d}-{arm}"
        folder.mkdir()
        with (folder / "process.log").open("w", encoding="utf-8") as log:
            result = run_logged(
                [
                    sys.executable,
                    str(stage / "executors/health_probe.py"),
                    "--source",
                    str(campaign / "sources" / arm / "api"),
                    "--output",
                    str(folder / "samples.json"),
                    "--budget",
                    str(min(15, remaining - 5)),
                ],
                cwd=campaign / "sources" / arm / "api",
                env=dict(os.environ, QUALITY_CAMPAIGN_RUN=str(campaign)),
                log=log,
                timeout=min(30, remaining),
            )
        if result.returncode != 0:
            cells.append(
                {
                    "arm": arm,
                    "state": "INFRASTRUCTURE_FAILURE",
                    "exit_code": result.returncode,
                }
            )
            break
        data = json.loads((folder / "samples.json").read_text(encoding="utf-8"))
        cells.append(
            {
                "arm": arm,
                "state": data["state"],
                "n": len(data["samples_ms"]),
                "response_sha256": data["response_sha256"],
                "evidence": str((folder / "samples.json").relative_to(campaign)),
            }
        )
    save(output / "platform_after.json", windows_metadata())
    summary = {
        "state": "EXPLORATORY_COMPONENT_ONLY",
        "cells": cells,
        "elapsed_seconds": time.perf_counter() - started,
        "total_requests": sum(c.get("n", 0) for c in cells),
        "all_registered_cells_complete": len(cells) == 12
        and all(c["state"] == "COLLECTED_EXPLORATORY" for c in cells),
        "response_integrity": "EQUAL_ACROSS_COLLECTED_CELLS"
        if len({c.get("response_sha256") for c in cells if c.get("n", 0)}) == 1
        else "NOT_ESTABLISHED",
        "p99": None,
        "confirmed_gain": False,
        "limitations": [
            "No phone, SQL/catalog/IA workload or sustained throughput claim",
            "Budgets/unstable warmup remain explicit; no favorable stopping",
            "Even finite conditional CI and three block ratios cannot certify a gain without host/dependence qualification",
        ],
    }
    if summary["all_registered_cells_complete"]:
        pairs = []
        for block in range(3):
            group = cells[block * 4 : (block + 1) * 4]
            a = []
            b = []
            for cell in group:
                data = json.loads(
                    (campaign / cell["evidence"]).read_text(encoding="utf-8")
                )
                (a if cell["arm"] == "A" else b).extend(data["samples_ms"])
            pairs.append((a, b))
        summary["paired_block_p95_ratio_exploratory"] = paired_block_interval(pairs)
    save(output / "summary.json", summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in [
                    "state",
                    "total_requests",
                    "all_registered_cells_complete",
                    "confirmed_gain",
                ]
            }
        )
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.run, args.attempt))
