"""Private files and this runner's DB only; no user container mutations."""

import os
from critical_suite import BASE, EXECUTORS, execute, require_green


def test_storage_and_database_recovery():
    filters = os.environ.get("QUALITY_STORAGE_FILTER", "all")
    assert filters in {"all", "revalidate"}
    path = str(EXECUTORS / "storage_resilience_cases.py")
    selected = (
        [path]
        if filters == "all"
        else [
            path + "::" + name
            for name in (
                "test_IMG07_missing_private_file_then_restored",
                "test_RES05_disposable_database_short_outage_and_recovery",
            )
        ]
    )
    result = execute(
        BASE,
        "storage-resilience-baseline",
        selected,
        timeout_seconds=240,
    )
    require_green(result)
