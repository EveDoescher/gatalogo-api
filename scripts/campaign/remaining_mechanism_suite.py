"""Bounded account/galery/report/resource variants on disposable DB/storage."""

import os
from critical_suite import BASE, EXECUTORS, execute, require_green


def test_remaining_mechanisms():
    path = str(EXECUTORS / "remaining_mechanism_cases.py")
    selected = (
        [path]
        if os.environ.get("QUALITY_MECHANISM_FILTER") != "money"
        else [
            path
            + "::test_LOAD03_DATA07_distinct_intents_compete_for_one_purchase_balance"
        ]
    )
    result = execute(
        BASE,
        "mechanisms-baseline",
        selected,
        timeout_seconds=300,
    )
    require_green(result)
