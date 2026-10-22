"""Real localhost HTTP/SQLite restart test with disposable API accounts."""

from critical_suite import BASE, execute, require_green


def test_live_flutter_restart_and_http_commit_loss():
    result = execute(
        BASE,
        "live-baseline",
        [str(BASE / "tests/test_quintal_live_api.py")],
        timeout_seconds=900,
    )
    require_green(result)
