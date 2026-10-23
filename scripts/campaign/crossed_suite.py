"""Run both frozen Flutter clients against a declared isolated API revision."""

import os
from critical_suite import RUN, EXECUTORS, execute, require_green


def test_crossed_versions():
    arm = os.environ.get("QUALITY_API_ARM")
    assert arm in {"A", "B"}
    result = execute(
        RUN / "sources" / arm / "api",
        "crossed-api-" + arm,
        [str(EXECUTORS / "crossed_cases.py")],
        timeout_seconds=1080,
    )
    require_green(result)
