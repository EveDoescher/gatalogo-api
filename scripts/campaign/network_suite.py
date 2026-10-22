"""Real local TCP security characterization with disposable infrastructure."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_real_websocket_invalidation():
    result = execute(
        BASE,
        "network-baseline",
        [str(EXECUTORS / "network_cases.py")],
        timeout_seconds=180,
    )
    require_green(result)
