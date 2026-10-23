"""Audit the real API/Flutter wire contract; findings are not approval gates."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_wire_consumer():
    result = execute(
        BASE,
        "wire-contract-baseline",
        [str(EXECUTORS / "wire_contract_cases.py")],
        timeout_seconds=360,
    )
    require_green(result)
