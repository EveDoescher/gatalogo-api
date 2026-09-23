from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import realtime_events


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["push", "websocket"])
async def test_delivery_failure_does_not_fail_committed_operation_or_next_event(
    monkeypatch, mock_push_delivery, channel
):
    session = SimpleNamespace(info={})
    websocket = AsyncMock()
    monkeypatch.setattr(realtime_events.ws_manager, "send_to_user", websocket)
    failing_sender = mock_push_delivery if channel == "push" else websocket
    failing_sender.side_effect = [RuntimeError("delivery unavailable"), None]
    for _ in range(2):
        realtime_events.queue_event(
            session, user_id=uuid4(), event="new_notification",
            data={"notification_id": str(uuid4())},
        )
    await realtime_events.publish_committed_events(session)
    assert websocket.await_count == 2
    assert mock_push_delivery.await_count == 2
    await realtime_events.publish_committed_events(session)
    assert websocket.await_count == 2
