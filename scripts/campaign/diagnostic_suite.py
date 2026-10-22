"""Live rewards scenarios require a declared disposable rollout variant."""

import os
from critical_suite import BASE, EXECUTORS, execute, require_green


def test_live_routine_diagnostic():
    variant = os.environ.get("QUALITY_LIVE_REWARDS_VARIANT")
    assert variant in {"enabled", "disabled"}, (
        "Declare enabled/disabled variant explicitly"
    )
    name = (
        "test_live_routine_reward_diagnostic"
        if variant == "enabled"
        else "test_live_disabled_rewards_activity"
    )
    result = execute(
        BASE,
        "diagnostic-baseline",
        [str(EXECUTORS / "live_diagnostic_cases.py") + "::" + name],
        timeout_seconds=360,
    )
    require_green(result)
