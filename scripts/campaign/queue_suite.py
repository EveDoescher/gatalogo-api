"""Bounded synthetic queues/fanout, no paid inference or prolonged load."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_bounded_queues_and_fanout():
    result = execute(
        BASE,
        "queue-baseline",
        [
            str(EXECUTORS / "extended_cases.py") + "::" + name
            for name in (
                "test_LOAD04_controlled_reference_queue",
                "test_LOAD05_manager_fanout_and_cleanup",
            )
        ],
    )
    require_green(result)
