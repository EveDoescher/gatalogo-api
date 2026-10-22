"""Bounded real loopback WebSockets, disposable runner only; no user backend."""

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
assert SOURCE.is_relative_to(Path(os.environ["QUALITY_CAMPAIGN_RUN"]) / "sources")
sys.path.insert(0, str(SOURCE / "tests"))
pytest_plugins = ["test_quintal_live_api"]
from app.config import get_settings
from app.persistence_models import (
    Conversation,
    ConversationMember,
    Friendship,
    User,
    UserSession,
)
from app.services.auth_service import issue_tokens


def token(client):
    return client.headers["authorization"].removeprefix("Bearer ")


def claims(value):
    settings = get_settings()
    return jwt.decode(
        value, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer
    )


def record(name, data):
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ".json")).write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


async def read_event(ws, expected):
    async with asyncio.timeout(5):
        while True:
            item = json.loads(await ws.recv())
            if item.get("event") == expected:
                return item


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["revoked", "expired-session"])
async def test_SEC10_tcp_preexisting_socket_after_session_invalidated(live, defect):
    parsed = urlparse(live.url)
    assert parsed.hostname == "127.0.0.1" and parsed.port != 8000
    assert make_url(get_settings().database_url).database == "recognition_test"
    chat, outsider = uuid4(), uuid4()
    async with live.db() as db:
        user = User(
            id=outsider,
            email=f"ws-campaign-{outsider.hex}@example.invalid",
            status="active",
        )
        db.add_all([user, Conversation(id=chat)])
        await db.flush()
        db.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=u)
                for u in (live.uid, live.other_uid)
            ]
        )
        db.add_all(
            [
                Friendship(user_id=a, friend_id=b)
                for a, b in ((live.uid, live.other_uid), (live.other_uid, live.uid))
            ]
        )
        outsider_token = (
            await issue_tokens(db, user=user, device_id="campaign-outsider")
        )["access_token"]
        await db.commit()
    base = live.url.replace("http://", "ws://") + "/ws?token="
    try:
        async with AsyncExitStack() as stack:
            author = await stack.enter_async_context(
                connect(
                    base + token(live.a), open_timeout=5, close_timeout=2, proxy=None
                )
            )
            observer = await stack.enter_async_context(
                connect(
                    base + token(live.other),
                    open_timeout=5,
                    close_timeout=2,
                    proxy=None,
                )
            )
            foreign = await stack.enter_async_context(
                connect(
                    base + outsider_token, open_timeout=5, close_timeout=2, proxy=None
                )
            )
            # Positive control: this observer actually receives authorized traffic.
            await author.send(
                json.dumps({"type": "typing", "conversation_id": str(chat)})
            )
            first = await read_event(observer, "user_typing")
            assert first["data"]["user_id"] == str(live.uid)
            # Invalid and unauthorized inputs do not terminate the connection.
            for item in (
                "{",
                "[]",
                "null",
                '{"type":1}',
                '{"type":"typing","conversation_id":"invalid"}',
                json.dumps({"type": "typing", "conversation_id": str(chat)}),
            ):
                await foreign.send(item)
            await foreign.send("ping")
            assert await asyncio.wait_for(foreign.recv(), 5) == "pong"
            try:
                unexpected = await asyncio.wait_for(observer.recv(), 0.3)
            except TimeoutError:
                unexpected = None
            assert unexpected is None, "Outsider must not generate recipient traffic"
            async with live.db() as db:
                session = await db.get(UserSession, UUID(claims(token(live.a))["sid"]))
                if defect == "revoked":
                    session.revoked_at = datetime.now(UTC)
                else:
                    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await db.commit()
            assert (await live.a.get("/auth/me")).status_code == 401
            try:
                async with connect(
                    base + token(live.a), open_timeout=5, close_timeout=2, proxy=None
                ):
                    pytest.fail("Invalid session unexpectedly opened a new socket")
            except InvalidStatus as rejection:
                assert rejection.response.status_code == 403
            # Characterization of the observed gap, not a passing security contract.
            await author.send(
                json.dumps(
                    {"type": "typing", "conversation_id": str(chat), "is_typing": False}
                )
            )
            late = await read_event(observer, "user_typing")
            assert late["data"]["is_typing"] is False
            record(
                "SEC10-tcp-" + defect,
                {
                    "scenario": "SEC-10",
                    "variant": defect,
                    "http_after_invalidation": 401,
                    "new_websocket_http_upgrade": 403,
                    "preexisting_socket_typing_events_received": 1,
                    "authorized_positive_control": True,
                    "outsider_observation_window_seconds": 0.3,
                    "transport": "real TCP loopback, own disposable API",
                    "finding": "Existing connection still delivers typing after invalidation; observation is not proof of all actions or unbounded absence of foreign traffic",
                },
            )
    finally:
        async with live.db() as db:
            await db.execute(delete(Conversation).where(Conversation.id == chat))
            await db.execute(
                delete(User).where(
                    User.id == outsider,
                    User.email == f"ws-campaign-{outsider.hex}@example.invalid",
                )
            )
            await db.commit()
