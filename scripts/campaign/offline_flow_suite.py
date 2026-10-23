"""Five bounded desktop client processes against an isolated real API."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_offline_flow():
    result = execute(
        BASE,
        "offline-flow-baseline",
        [str(EXECUTORS / "offline_flow_cases.py")],
        timeout_seconds=900,
    )
    require_green(result)
