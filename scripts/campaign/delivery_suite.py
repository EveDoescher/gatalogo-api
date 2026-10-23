"""Bounded real Mailpit recovery and delivery-failure variants."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_delivery_variants():
    result = execute(
        BASE,
        "delivery-baseline",
        [str(EXECUTORS / "delivery_cases.py")],
        timeout_seconds=240,
    )
    require_green(result)
