"""Directed Postgres-only mutation windows and a real own-worker crash."""

import os
from critical_suite import BASE, EXECUTORS, execute, require_green


def test_worker_races():
    result = execute(
        BASE,
        "worker-race-baseline",
        [
            str(EXECUTORS / "worker_race_cases.py"),
            "-k",
            "postgres and actual_worker_process"
            if os.environ.get("QUALITY_WORKER_FILTER") == "process"
            else "postgres",
        ],
        timeout_seconds=240,
    )
    require_green(result)
