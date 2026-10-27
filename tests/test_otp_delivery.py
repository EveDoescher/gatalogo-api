from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services import auth_service


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_delivery_requires_successful_commit(monkeypatch, commit_fails):
    events = []

    async def commit():
        events.append("commit")
        if commit_fails:
            raise RuntimeError("controlled database failure")

    async def send(**kwargs):
        events.append("send")
        assert kwargs["code"] == "123456"

    session = AsyncMock()
    session.commit.side_effect = commit
    monkeypatch.setattr(auth_service, "_send_otp", send)
    delivery = auth_service.OtpDelivery(
        "synthetic@example.com", "verify_email", "123456",
        Settings(_env_file=None, smtp_enabled=False),
    )
    assert "123456" not in repr(delivery)
    if commit_fails:
        with pytest.raises(RuntimeError):
            await auth_service.commit_and_send_otp(session, delivery)
        assert events == ["commit"]
    else:
        await auth_service.commit_and_send_otp(session, delivery)
        assert events == ["commit", "send"]
