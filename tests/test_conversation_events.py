import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException, WebSocketDisconnect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.persistence_models import (
    User,
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
)
from app.routers import websocket
from app.services.conversations import mark_messages_read


@pytest_asyncio.fixture
async def conversation(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "chat.db"))
    async with engine.begin() as connection:
        for model in (
            User,
            Conversation,
            ConversationMember,
            ConversationMessage,
            Friendship,
        ):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        alice, bob, stranger = [
            User(id=uuid4(), email=f"{uuid4()}@test.local", username=name)
            for name in ("alice", "bob", "stranger")
        ]
        chat = Conversation(id=uuid4())
        session.add_all([alice, bob, stranger, chat])
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat.id, user_id=u.id)
                for u in (alice, bob)
            ]
        )
        session.add_all(
            [
                Friendship(user_id=a.id, friend_id=b.id)
                for a, b in ((alice, bob), (bob, alice))
            ]
        )
        message = ConversationMessage(
            id=uuid4(), conversation_id=chat.id, sender_id=bob.id, body="Olha o gato"
        )
        session.add(message)
        await session.commit()
    monkeypatch.setattr(websocket.ws_manager, "send_to_user", AsyncMock())
    yield SimpleNamespace(
        factory=factory,
        alice=alice,
        bob=bob,
        stranger=stranger,
        chat_id=chat.id,
        message_id=message.id,
    )
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["typing", "read"])
async def test_stranger_cannot_emit_conversation_events_or_read_messages(
    conversation, kind
):
    c = conversation
    with pytest.raises(HTTPException) as rejected:
        await websocket._conversation_event(
            c.factory, c.stranger, {"type": kind, "conversation_id": str(c.chat_id)}
        )
    assert rejected.value.status_code == 403
    websocket.ws_manager.send_to_user.assert_not_awaited()
    async with c.factory() as session:
        assert (await session.get(ConversationMessage, c.message_id)).read_at is None


@pytest.mark.asyncio
async def test_http_and_websocket_read_share_authorization_and_receipts(conversation):
    c = conversation
    await websocket._conversation_event(
        c.factory, c.alice, {"type": "read", "conversation_id": str(c.chat_id)}
    )
    websocket.ws_manager.send_to_user.assert_awaited_once()
    args = websocket.ws_manager.send_to_user.await_args.args
    assert args[0] == c.bob.id and args[1] == "messages_read"
    async with c.factory() as session:
        assert (
            await session.get(ConversationMessage, c.message_id)
        ).read_at is not None
        assert await mark_messages_read(session, c.chat_id, c.alice.id) == 0
        with pytest.raises(HTTPException):
            await mark_messages_read(session, c.chat_id, c.stranger.id)


@pytest.mark.asyncio
async def test_typing_for_member_reaches_only_other_member(conversation):
    c = conversation
    await websocket._conversation_event(
        c.factory,
        c.alice,
        {"type": "typing", "conversation_id": str(c.chat_id), "is_typing": False},
    )
    args = websocket.ws_manager.send_to_user.await_args.args
    assert args[0] == c.bob.id and args[1] == "user_typing"
    assert args[2]["user_id"] == str(c.alice.id) and not args[2]["is_typing"]


@pytest.mark.asyncio
async def test_invalid_envelopes_keep_connection_alive_and_cleanup_runs_once(
    conversation, monkeypatch
):
    c = conversation
    monkeypatch.setattr(websocket, "get_session_factory", lambda: c.factory)
    monkeypatch.setattr(
        websocket, "authenticated_user", AsyncMock(return_value=c.alice)
    )
    monkeypatch.setattr(websocket.ws_manager, "connect", AsyncMock())
    monkeypatch.setattr(websocket.ws_manager, "disconnect", AsyncMock())
    frames = [
        "not-json",
        "[]",
        "null",
        json.dumps({"type": []}),
        json.dumps({"type": "read", "conversation_id": "invalid"}),
        "ping",
        json.dumps({"type": "ping"}),
        WebSocketDisconnect(),
    ]
    socket = SimpleNamespace(
        receive_text=AsyncMock(side_effect=frames),
        send_text=AsyncMock(),
        close=AsyncMock(),
    )
    await websocket.websocket_endpoint(socket, token="valid")
    assert socket.send_text.await_count == 2
    websocket.ws_manager.disconnect.assert_awaited_once_with(c.alice.id, socket)
    async with c.factory() as session:
        assert (await session.get(User, c.alice.id)).last_seen_at is not None
