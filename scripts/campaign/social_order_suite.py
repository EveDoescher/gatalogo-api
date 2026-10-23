"""Compare the tie-order contract in both frozen API versions; preserve failures."""

import pytest

from critical_suite import RUN, EXECUTORS, execute, require_green


@pytest.mark.parametrize("arm", ["A", "B"])
def test_social_order(arm):
    result = execute(
        RUN / "sources" / arm / "api",
        "social-order-" + arm,
        [str(EXECUTORS / "social_order_cases.py")],
    )
    require_green(result)
