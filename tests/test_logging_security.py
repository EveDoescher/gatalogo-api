import logging

import pytest

from app.logging_security import SensitiveQueryFilter


@pytest.mark.parametrize(
    "name",
    [
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "key",
        "password",
        "code",
        "%74oken",
        "ToKeN",
    ],
)
def test_SEC08_query_secret_is_absent_from_rendered_and_structured_log(name):
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        0,
        "WebSocket %s",
        (f"/ws?{name}=PRIVATE_CANARY&safe=VISIBLE",),
        None,
    )
    assert SensitiveQueryFilter().filter(record)
    assert "PRIVATE_CANARY" not in record.getMessage()
    assert "PRIVATE_CANARY" not in repr(record.args)
    assert "safe=VISIBLE" in record.getMessage()


def test_SEC08_nonsensitive_log_context_is_preserved():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        0,
        "Request %s",
        ("/cats?page=2&sort=name",),
        None,
    )
    SensitiveQueryFilter().filter(record)
    assert record.getMessage() == "Request /cats?page=2&sort=name"


def test_SEC08_repeated_secret_parameters_are_all_redacted():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        "%s",
        ("/ws?token=FIRST&token=SECOND",),
        None,
    )
    SensitiveQueryFilter().filter(record)
    assert record.getMessage() == "/ws?token=[redacted]&token=[redacted]"
