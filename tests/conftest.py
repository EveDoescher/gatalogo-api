"""Keep test transactions independent from external notification delivery."""
from unittest.mock import AsyncMock

import pytest

from app.services.fcm_service import fcm_sender


@pytest.fixture(autouse=True)
def mock_push_delivery(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(fcm_sender, "send_notification", sender)
    return sender
