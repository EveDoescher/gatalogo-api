"""Small group so infrastructure cost cannot erase broad campaign evidence."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_remaining_security_and_bounded_concurrency():
    result = execute(
        BASE,
        "targeted-baseline",
        [
            str(EXECUTORS / "extended_cases.py") + "::" + name
            for name in (
                "test_SEC10_open_websocket_revocation_window_observation",
                "test_SEC06_bounded_upload_and_schema_limits",
                "test_LOAD03_same_intent_bounded_barrier",
                "test_DLV02_smtp_accepted_before_database_commit_failure",
            )
        ],
    )
    require_green(result)
