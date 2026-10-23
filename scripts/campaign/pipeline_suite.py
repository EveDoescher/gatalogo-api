"""Server photo flow and bounded fake-inference concurrency; no real models."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_pipeline_mechanisms():
    result = execute(
        BASE,
        "pipeline-baseline",
        [str(EXECUTORS / "pipeline_cases.py")],
        timeout_seconds=240,
    )
    require_green(result)
