"""Run local positive/negative provider-boundary controls on both versions."""

import pytest

from critical_suite import EXECUTORS, RUN, execute, require_green


@pytest.mark.parametrize("arm", ["A", "B"])
def test_identity_controls(arm):
    result = execute(
        RUN / "sources" / arm / "api",
        "identity-controls-" + arm,
        [str(EXECUTORS / "identity_controls_cases.py")],
    )
    require_green(result)
