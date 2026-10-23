"""Bounded conversation/feed query counts and cursor/privacy integrity."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_social_volumes():
    result = execute(
        BASE,
        "social-volume-baseline",
        [str(EXECUTORS / "social_volume_cases.py")],
        timeout_seconds=240,
    )
    require_green(result)
