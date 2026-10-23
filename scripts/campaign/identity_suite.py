"""Preserve red provider-authentication contracts in both frozen API versions."""

import pytest

from critical_suite import EXECUTORS, RUN, execute, require_green


@pytest.mark.parametrize("arm", ["A", "B"])
def test_identity_negative_contracts(arm):
    summary = execute(
        RUN / "sources" / arm / "api",
        "identity-" + arm,
        [str(EXECUTORS / "identity_cases.py")],
    )
    require_green(summary)
