"""Bounded migrated Postgres query-plan diagnostics."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_query_plans():
    result = execute(
        BASE,
        "query-plan-baseline",
        [str(EXECUTORS / "query_plan_cases.py")],
        timeout_seconds=240,
    )
    require_green(result)
