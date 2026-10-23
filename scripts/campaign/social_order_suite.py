"""Keep the tie-order invariance contract red if the behavior is reproduced."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_social_order():
    result = execute(
        BASE, "social-order-baseline", [str(EXECUTORS / "social_order_cases.py")]
    )
    require_green(result)
