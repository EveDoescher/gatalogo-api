"""Run three short owned upload transport fault windows."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_upload_transport():
    result = execute(
        BASE,
        "upload-transport-baseline",
        [str(EXECUTORS / "upload_transport_cases.py")],
    )
    require_green(result)
