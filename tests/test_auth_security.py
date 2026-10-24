"""SEC-01 HTTP and ASGI WebSocket contracts on disposable PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_database_session
from app.main import app
from app.persistence_models import AuthIdentity, User, UserSession
from app.routers import auth, websocket
from app.services.auth_service import issue_tokens
from app.services.websocket_manager import ConnectionManager
pytest_plugins = ["test_apple_identity"]


@pytest_asyncio.fixture
async def accounts(monkeypatch):
    url = os.environ.get("RECOGNITION_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Requires scripts/run_security_regression.py disposable PostgreSQL")
    assert url.endswith("/recognition_test") and ":5432/" not in url
    schema = "sec01_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema + ",public"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    previous = dict(app.dependency_overrides)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        async with factory() as session:
            first = User(id=uuid4(), email="verified@example.com", status="active")
            other = User(id=uuid4(), email="other@example.com", status="active")
            session.add_all([first, other])
            await session.flush()
            bundle = await issue_tokens(session, user=first, device_id="security-test")
            await session.commit()

        async def database():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_database_session] = database
        monkeypatch.setattr(websocket, "get_session_factory", lambda: factory)
        monkeypatch.setattr(websocket, "ws_manager", ConnectionManager())
        monkeypatch.setattr(get_settings(), "apple_client_id", "test-client")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(factory=factory, client=client, first=first, other=other,
                                  token=bundle["access_token"])
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def counts(factory):
    async with factory() as session:
        return [await session.scalar(select(func.count()).select_from(model))
                for model in (User, UserSession, AuthIdentity)]


async def invalid_token(accounts, variant):
    settings = get_settings()
    payload = jwt.decode(accounts.token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer)
    if variant == "missing":
        return None
    if variant == "malformed":
        return "not-a-token"
    for key in ("exp", "iat", "sid", "sub", "iss", "type"):
        if variant == "missing-" + key:
            payload.pop(key)
    if variant == "expired":
        payload["exp"] = 1
    if variant == "wrong-issuer":
        payload["iss"] = "other"
    if variant == "wrong-type":
        payload["type"] = "refresh"
    if variant == "null-session":
        payload["sid"] = None
    if variant == "malformed-user":
        payload["sub"] = "bad-id"
    if variant == "foreign-session":
        payload["sub"] = str(accounts.other.id)
    if variant == "unknown-session":
        payload["sid"] = str(uuid4())
    if variant in {"revoked", "session-expired", "inactive"}:
        async with accounts.factory() as session:
            row = await session.get(UserSession, payload["sid"])
            user = await session.get(User, accounts.first.id)
            if variant == "revoked":
                row.revoked_at = datetime.now(UTC)
            elif variant == "session-expired":
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                user.status = "deactivated"
            await session.commit()
    return jwt.encode(payload, "wrong" * 12 if variant == "wrong-signature" else settings.jwt_secret,
                      algorithm="HS256")


class Socket:
    """Actual ASGI websocket exchange; no network or second event loop."""
    def __init__(self, token):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.token = token

    async def __aenter__(self):
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
                 "path": "/ws", "raw_path": b"/ws", "root_path": "", "headers": [],
                 "query_string": ("token=" + (self.token or "")).encode(),
                 "client": ("127.0.0.1", 12345), "server": ("test", 80), "subprotocols": []}
        await self.incoming.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(app(scope, self.incoming.get, self.outgoing.put))
        return self

    async def event(self):
        return await asyncio.wait_for(self.outgoing.get(), timeout=5)

    async def __aexit__(self, *args):
        await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self.task, timeout=5)
        finally:
            if not self.task.done():
                self.task.cancel()


VARIANTS = ["missing", "malformed", "wrong-signature", "wrong-issuer", "wrong-type", "expired",
            "missing-exp", "missing-iat", "missing-sub", "missing-sid", "missing-iss", "missing-type",
            "null-session", "malformed-user", "foreign-session", "unknown-session",
            "revoked", "session-expired", "inactive"]


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", VARIANTS)
async def test_invalid_auth_rejected_http_and_websocket(accounts, variant):
    token = await invalid_token(accounts, variant)
    before = await counts(accounts.factory)
    response = await accounts.client.get("/sync/pull?cursor=0&limit=1",
                                         headers={"Authorization": "Bearer " + token} if token else {})
    assert response.status_code == 401
    async with Socket(token) as socket:
        event = await socket.event()
        assert event["type"] == "websocket.close" and event["code"] == 1008
    assert await counts(accounts.factory) == before


@pytest.mark.asyncio
async def test_valid_auth_http_and_websocket(accounts):
    response = await accounts.client.get("/sync/pull?cursor=0&limit=1",
                                         headers={"Authorization": "Bearer " + accounts.token})
    assert response.status_code == 200
    async with Socket(accounts.token) as socket:
        assert (await socket.event())["type"] == "websocket.accept"
        await socket.incoming.put({"type": "websocket.receive", "text": "ping"})
        assert (await socket.event())["text"] == "pong"


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["revoked", "session-expired", "inactive"])
@pytest.mark.parametrize("frame", ["ping", json.dumps({"type": "typing", "conversation_id": str(uuid4())}),
                                  json.dumps({"type": "read", "conversation_id": str(uuid4())})])
async def test_open_socket_revalidates_before_handling_frames(accounts, monkeypatch, variant, frame):
    from unittest.mock import AsyncMock
    command = AsyncMock()
    monkeypatch.setattr(websocket, "_conversation_event", command)
    async with Socket(accounts.token) as socket:
        assert (await socket.event())["type"] == "websocket.accept"
        await invalid_token(accounts, variant)
        await socket.incoming.put({"type": "websocket.receive", "text": frame})
        event = await socket.event()
        assert event["type"] == "websocket.close" and event["code"] == 1008
        command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["revoked", "session-expired", "inactive"])
async def test_idle_socket_revalidates_without_client_heartbeat(accounts, monkeypatch, variant):
    monkeypatch.setattr(websocket, "SESSION_RECHECK_SECONDS", 0.05)
    async with Socket(accounts.token) as socket:
        assert (await socket.event())["type"] == "websocket.accept"
        await invalid_token(accounts, variant)
        event = await socket.event()
        assert event["type"] == "websocket.close" and event["code"] == 1008


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["wrong-audience", "wrong-issuer", "expired", "unsigned",
                                   "wrong-signature", "unverified-email", "subject-override"])
async def test_apple_rejections_have_no_database_effects(accounts, signer, variant):
    changes = {"wrong-audience": {"aud": "other"}, "wrong-issuer": {"iss": "https://appleid.apple.com.evil.invalid"},
               "expired": {"exp": 1}, "unverified-email": {"email_verified": False}}
    token = signer(**changes.get(variant, {}))
    if variant in {"unsigned", "wrong-signature"}:
        claims = jwt.decode(token, options={"verify_signature": False})
        token = jwt.encode(claims, "" if variant == "unsigned" else "untrusted" * 8,
                           algorithm="none" if variant == "unsigned" else "HS256")
    before = await counts(accounts.factory)
    response = await accounts.client.post("/auth/apple", json={"id_token": token,
        "user_identifier": "other-subject" if variant == "subject-override" else "test-subject"})
    assert response.status_code == 401
    assert await counts(accounts.factory) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["verified@example.com", None])
async def test_apple_verified_identity_reused_and_request_email_ignored(accounts, signer, email):
    for _ in range(2):
        response = await accounts.client.post("/auth/apple", json={
            "id_token": signer(email=email), "email": accounts.other.email,
            "user_identifier": "test-subject", "device_id": "test-device"})
        assert response.status_code == 200
        assert response.json()["user_id"] != str(accounts.other.id)
        if email:
            assert response.json()["user_id"] == str(accounts.first.id)
    async with accounts.factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthIdentity)) == 1
        assert await session.scalar(select(func.count()).select_from(User)) == (2 if email else 3)


@pytest.mark.asyncio
async def test_apple_unconfigured_does_not_fetch_keys(accounts, monkeypatch):
    monkeypatch.setattr(get_settings(), "apple_client_id", None)
    def forbidden(*args):
        pytest.fail("Unconfigured provider must not fetch keys")
    monkeypatch.setattr(auth, "verify_apple_identity", forbidden)
    response = await accounts.client.post("/auth/apple", json={"id_token": "test-not-a-token-000000"})
    assert response.status_code == 503
