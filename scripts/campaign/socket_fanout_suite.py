"""Real message fanout at 1/10/50 sockets, reconnect and bounded lag."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_real_socket_fanout():
    result = execute(
        BASE,
        "socket-fanout-baseline",
        [str(EXECUTORS / "socket_fanout_cases.py")],
        timeout_seconds=240,
    )
    require_green(result)
