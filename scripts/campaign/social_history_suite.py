"""History-volume matrix with a hard per-cell fixture bound and short reads."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_social_history():
    result = execute(
        BASE,
        "social-history-baseline",
        [str(EXECUTORS / "social_history_cases.py")],
        timeout_seconds=600,
    )
    require_green(result)
