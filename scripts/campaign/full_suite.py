"""Full frozen API regressions, including existing opt-in disposable integrations."""

import shutil
from critical_suite import BASE, RUN, ATTEMPT, execute, require_green


def preserve_client_evidence(stage):
    build = RUN / "sources/B/flutter/build"
    output = ATTEMPT / ("live_client_" + stage)
    output.mkdir(exist_ok=False)
    for path in build.glob("quintal-live-client-*"):
        if path.is_file():
            shutil.copy2(path, output / path.name)
        elif path.is_dir():
            shutil.copytree(path, output / path.name)


def test_full_frozen_api_regressions():
    preserve_client_evidence("before")
    try:
        result = execute(
            BASE, "full-baseline", [str(BASE / "tests")], timeout_seconds=1200
        )
    finally:
        preserve_client_evidence("after")
    require_green(result)
