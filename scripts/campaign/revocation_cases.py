"""Measure default WebSocket reauthentication using real time and disposable sessions."""

import asyncio
from datetime import UTC, datetime
import json
import time
from uuid import UUID

import jwt
import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence
from test_auth_security import Socket
from app.config import get_settings
from app.persistence_models import UserSession, SyncChange
from app.routers import websocket


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["revoked", "expired"])
@pytest.mark.parametrize("mode", ["idle", "next-frame"])
async def test_SEC10_real_default_revocation_window(db, monkeypatch, reason, mode):
    assert websocket.SESSION_RECHECK_SECONDS == 30
    monkeypatch.setattr(websocket, "get_session_factory", lambda: db.factory)
    settings = get_settings()
    token = db.tokens["A"]
    claims = jwt.decode(
        token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer
    )
    if reason == "expired":
        claims["exp"] = int(time.time()) + 3
        token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    async with Socket(token) as socket:
        assert (await socket.event())["type"] == "websocket.accept"
        await socket.incoming.put({"type": "websocket.receive", "text": "ping"})
        assert (await socket.event())["text"] == "pong"
        if reason == "revoked":
            async with db.factory() as session:
                (
                    await session.get(UserSession, UUID(claims["sid"]))
                ).revoked_at = datetime.now(UTC)
                await session.commit()
        else:
            await asyncio.sleep(max(0, claims["exp"] - time.time() + 0.02))
        invalidated_at = time.perf_counter()
        if mode == "next-frame":
            await socket.incoming.put(
                {"type": "websocket.receive", "text": json.dumps({"type": "ping"})}
            )
        close = await asyncio.wait_for(
            socket.outgoing.get(), timeout=45 if mode == "idle" else 5
        )
        elapsed = time.perf_counter() - invalidated_at
        assert close["type"] == "websocket.close" and close["code"] == 1008
    async with Socket(token) as denied:
        close = await denied.event()
        assert close["type"] == "websocket.close" and close["code"] == 1008
    response = await db.client.get(
        "/sync/pull", headers={"Authorization": "Bearer " + token}
    )
    assert response.status_code == 401
    assert (
        await db.client.get("/sync/pull", headers=db.headers("B"))
    ).status_code == 200
    async with db.factory() as session:
        assert await session.scalar(select(func.count()).select_from(SyncChange)) == 0
    evidence(
        "SEC10-real-window-" + reason + "-" + mode,
        dict(
            reason=reason,
            mode=mode,
            configured_idle_check_seconds=30,
            seconds_from_invalidation_observed_to_close=elapsed,
            close_code=1008,
            reopened_connection=1008,
            http_after_invalidation=401,
            other_account_http=200,
            clock="real monotonic; no shortened timeout or frozen wall clock",
            transport="actual ASGI WebSocket protocol; excludes TCP/proxy scheduling",
        ),
    )
