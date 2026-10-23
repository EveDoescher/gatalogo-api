"""Generated images, fake provider and real private caches; no paid inference."""

from critical_suite import BASE, EXECUTORS, execute, require_green


def test_image_mechanisms():
    result = execute(
        BASE, "image-baseline", [str(EXECUTORS / "image_cases.py")], timeout_seconds=240
    )
    require_green(result)
