"""SEC-03 real protocol loop, membership and persisted read receipts."""

from contextlib import AsyncExitStack
import json
from unittest.mock import AsyncMock

import pytest

pytest_plugins = ["security_cases"]
from security_cases import evidence, main, uid
from test_auth_security import Socket, app as socket_app
from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
)
from app.routers import websocket


@pytest.mark.asyncio
async def test_SEC03_asgi_loop_rejects_nonmember_and_malformed_frames_with_positive_receipts(
    db, monkeypatch
):
    assert socket_app is main.app
    conversation, message = (
        uid("SEC03-protocol-conversation"),
        uid("SEC03-protocol-message"),
    )
    async with db.factory() as session:
        session.add(Conversation(id=conversation))
        await session.flush()
        session.add_all(
            ConversationMember(conversation_id=conversation, user_id=uid(actor))
            for actor in ("A", "B")
        )
        session.add_all(
            Friendship(user_id=uid(a), friend_id=uid(b))
            for a, b in (
                ("A", "B"),
                ("B", "A"),
                ("C", "A"),
                ("A", "C"),
                ("C", "B"),
                ("B", "C"),
            )
        )
        session.add(
            ConversationMessage(
                id=message,
                conversation_id=conversation,
                sender_id=uid("B"),
                body="PRIVATE_RECEIPT_CANARY",
            )
        )
        await session.commit()
    monkeypatch.setattr(websocket, "get_session_factory", lambda: db.factory)
    delivered = AsyncMock(wraps=websocket.ws_manager.send_to_user)
    monkeypatch.setattr(websocket.ws_manager, "send_to_user", delivered)
    invalid = [
        ("invalid-json", "not-json"),
        ("json-array", "[]"),
        ("json-null", "null"),
        ("missing-type", "{}"),
        ("nonstring-type", json.dumps({"type": []})),
        ("unknown-type", json.dumps({"type": "unknown"})),
        ("typing-null-id", json.dumps({"type": "typing", "conversation_id": None})),
        ("read-nonstring-id", json.dumps({"type": "read", "conversation_id": []})),
        (
            "typing-invalid-uuid",
            json.dumps({"type": "typing", "conversation_id": "invalid"}),
        ),
        (
            "read-invalid-uuid",
            json.dumps({"type": "read", "conversation_id": "invalid"}),
        ),
        (
            "nonmember-typing",
            json.dumps(
                {
                    "type": "typing",
                    "conversation_id": str(conversation),
                    "is_typing": True,
                }
            ),
        ),
        (
            "nonmember-read",
            json.dumps({"type": "read", "conversation_id": str(conversation)}),
        ),
    ]
    async with AsyncExitStack() as stack:
        sockets = {}
        for actor in ("A", "B", "C"):
            sockets[actor] = await stack.enter_async_context(Socket(db.tokens[actor]))
            assert (await sockets[actor].event())["type"] == "websocket.accept"
        for _, frame in invalid:
            await sockets["C"].incoming.put(
                {"type": "websocket.receive", "text": frame}
            )
            await sockets["C"].incoming.put(
                {"type": "websocket.receive", "text": "ping"}
            )
            pong = await sockets["C"].event()
            assert pong["type"] == "websocket.send" and pong["text"] == "pong"
        delivered.assert_not_awaited()
        async with db.factory() as session:
            assert (await session.get(ConversationMessage, message)).read_at is None
        await sockets["A"].incoming.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "typing",
                        "conversation_id": str(conversation),
                        "is_typing": True,
                    }
                ),
            }
        )
        event = json.loads((await sockets["B"].event())["text"])
        assert event["event"] == "user_typing" and event["data"]["user_id"] == str(
            uid("A")
        )
        await sockets["A"].incoming.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {"type": "read", "conversation_id": str(conversation)}
                ),
            }
        )
        event = json.loads((await sockets["B"].event())["text"])
        assert event["event"] == "messages_read" and event["data"][
            "conversation_id"
        ] == str(conversation)
        async with db.factory() as session:
            assert (await session.get(ConversationMessage, message)).read_at is not None
        assert all(call.args[0] != uid("C") for call in delivered.await_args_list)
    evidence(
        "SEC03-asgi-membership-and-envelope",
        {
            "scenario": "SEC-03",
            "transport": "ASGI WebSocket route and connection manager; real PostgreSQL",
            "invalid_variants": [name for name, _ in invalid],
            "surviving_pongs": len(invalid),
            "unauthorized_deliveries": 0,
            "unauthorized_receipt_changes": 0,
            "member_typing_delivered": True,
            "member_read_persisted_and_delivered": True,
            "nonmember_never_received_private_event": True,
            "physical_device": False,
        },
    )
