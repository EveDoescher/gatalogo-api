import json
from uuid import uuid4

import pytest

from app.services.websocket_manager import ConnectionManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_connection_manager_delivers_one_event_to_each_active_device():
    manager = ConnectionManager()
    user_id = uuid4()
    first, second = FakeWebSocket(), FakeWebSocket()

    await manager.connect(user_id, first)
    await manager.connect(user_id, second)
    await manager.send_to_user(
        user_id,
        "new_notification",
        {"notification_id": str(uuid4())},
    )

    assert first.accepted and second.accepted
    assert len(first.messages) == len(second.messages) == 1
    assert json.loads(first.messages[0])["event"] == "new_notification"


@pytest.mark.asyncio
async def test_disconnect_removes_the_socket_without_affecting_other_device():
    manager = ConnectionManager()
    user_id = uuid4()
    first, second = FakeWebSocket(), FakeWebSocket()

    await manager.connect(user_id, first)
    await manager.connect(user_id, second)
    await manager.disconnect(user_id, first)
    await manager.send_to_user(user_id, "new_message", {"conversation_id": str(uuid4())})

    assert first.messages == []
    assert len(second.messages) == 1
