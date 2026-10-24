"""Next campaign: resource controls, HTTP conflicts and filesystem rollback evidence."""

from datetime import timedelta
import hashlib
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases")
from security_cases import evidence, NOW, uid, RUN

pytest_plugins = ["security_cases"]
from app.persistence_models import (
    CatAvatar,
    CatPhoto,
    Conversation,
    ConversationMember,
    Friendship,
    RemoteCat,
    SyncChange,
    VisionJob,
)
from app.routers import sync


async def seed(db):
    data = (RUN / "fixtures/synthetic.png").read_bytes()
    async with db.factory() as session:
        cat = RemoteCat(
            id=uid("matrix-cat"),
            user_id=uid("B"),
            client_id=uid("matrix-client"),
            payload={"status": "pending_analysis", "is_owned": True},
            analysis={"authoritative": True},
            client_updated_at=NOW,
            updated_by_device="b",
        )
        session.add(cat)
        await session.flush()
        key, digest = db.storage.save(
            user_id=uid("B"), cat_id=cat.id, data=data, suffix=".png"
        )
        cat.photo_key, cat.photo_hash, cat.photo_content_type = key, digest, "image/png"
        for kind in ("primary", "front", "left", "right", "back"):
            session.add(
                CatPhoto(
                    cat_id=cat.id,
                    kind=kind,
                    storage_key=key,
                    content_hash=digest,
                    content_type="image/png",
                    status="ready",
                )
            )
        session.add(
            CatAvatar(
                cat_id=cat.id,
                status="ready",
                preview_key=key,
                texture_keys={"coat": key},
            )
        )
        await session.commit()
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "photo-read",
        "photo-write",
        "front-read",
        "front-write",
        "front-delete",
        "avatar-preview",
        "avatar-coat",
        "jobs",
        "enqueue",
        "report",
        "sighting",
    ],
)
async def test_SEC02_resource_action_actor_matrix(db, monkeypatch, action):
    data = await seed(db)
    # Scope is authorization/persistence; no recognition or generation execution.
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    monkeypatch.setattr(sync, "rebuild_analysis_from_photos", AsyncMock())
    base = f"/sync/cats/{uid('matrix-client')}"

    async def request(actor):
        headers = db.headers(actor) if actor else {}
        if action == "photo-read":
            return await db.client.get(base + "/photo", headers=headers)
        if action == "photo-write":
            return await db.client.put(
                base + "/photo",
                headers=headers,
                data={
                    "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                    "device_id": "new",
                },
                files={"image": ("safe.png", data, "image/png")},
            )
        if action == "front-read":
            return await db.client.get(
                base + "/reference-photos/front", headers=headers
            )
        if action == "front-write":
            return await db.client.put(
                base + "/reference-photos/front",
                headers=headers,
                files={"image": ("safe.png", data, "image/png")},
            )
        if action == "front-delete":
            return await db.client.delete(
                base + "/reference-photos/front", headers=headers
            )
        if action.startswith("avatar-"):
            return await db.client.get(
                base + "/avatar/" + action.removeprefix("avatar-"), headers=headers
            )
        if action == "jobs":
            return await db.client.get(
                f"/vision/cats/{uid('matrix-client')}/jobs", headers=headers
            )
        if action == "enqueue":
            return await db.client.post(
                f"/vision/cats/{uid('matrix-client')}/enqueue", headers=headers
            )
        if action == "report":
            return await db.client.post(
                "/vision/missing-reports",
                headers=headers,
                json={
                    "cat_client_id": str(uid("matrix-client")),
                    "latitude": -23.0,
                    "longitude": -46.0,
                    "radius_meters": 500,
                },
            )
        return await db.client.post(
            "/vision/sightings",
            headers=headers,
            json={
                "cat_client_id": str(uid("matrix-client")),
                "latitude": -23.0,
                "longitude": -46.0,
            },
        )

    observations = {}
    for actor in ("A", "C", None):
        response = await request(actor)
        observations[actor or "anonymous"] = response.status_code
        assert response.status_code == (401 if actor is None else 404)
        assert response.content != data
        async with db.factory() as session:
            cat = await session.get(RemoteCat, uid("matrix-cat"))
            assert cat.analysis == {"authoritative": True} and cat.deleted_at is None
            assert (
                await session.scalar(select(func.count()).select_from(VisionJob)) == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(SyncChange)) == 0
            )
            assert all(
                p.deleted_at is None
                for p in (await session.scalars(select(CatPhoto))).all()
            )
    response = await request("B")
    expected = (
        202
        if action in ("front-write", "enqueue")
        else 204
        if action in ("photo-write", "front-delete")
        else 201
        if action in ("report", "sighting")
        else 200
    )
    observations["B"] = response.status_code
    assert response.status_code == expected, response.text
    if action in ("photo-read", "front-read", "avatar-preview", "avatar-coat"):
        assert response.content == data
    evidence(
        "SEC02-matrix-" + action,
        {
            "scenario": "SEC-02",
            "action": action,
            "actors": observations,
            "foreign_state_unchanged": True,
            "owner_positive": True,
        },
    )


@pytest.mark.asyncio
async def test_SEC02_SEC03_chat_attachment_member_controls(db):
    chat = uid("attachment-chat")
    async with db.factory() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=uid(w))
                for w in ("A", "B")
            ]
        )
        session.add_all(
            [
                Friendship(user_id=uid(a), friend_id=uid(b))
                for a, b in (("A", "B"), ("B", "A"))
            ]
        )
        await session.commit()
    data = (RUN / "fixtures/synthetic.png").read_bytes()
    path = f"/social/conversations/{chat}/photo"
    foreign = await db.client.post(
        path, headers=db.headers("C"), files={"file": ("photo.png", data)}
    )
    assert foreign.status_code == 403
    assert not list(db.storage.root.rglob("*.png"))
    saved = await db.client.post(
        path, headers=db.headers("A"), files={"file": ("photo.png", data)}
    )
    assert saved.status_code == 200
    url = saved.json()["media_url"]
    for who, expected in (("A", 200), ("B", 200), ("C", 403), (None, 401)):
        response = await db.client.get(url, headers=db.headers(who) if who else {})
        assert response.status_code == expected
        if expected == 200:
            assert response.content == data
    query_reply = await db.client.get(url, params={"token": db.tokens["B"]})
    assert query_reply.status_code == 200 and query_reply.content == data
    evidence(
        "SEC02-chat-attachment",
        {
            "scenarios": ["SEC-02", "SEC-03"],
            "member_bytes_sha256": hashlib.sha256(data).hexdigest(),
            "member_query_token_positive": True,
            "nonmember": 403,
            "anonymous": 401,
        },
    )


@pytest.mark.asyncio
async def test_DATA05_http_conflicts_preserve_authoritative_analysis(db):
    await seed(db)

    async def push(who, seconds, device, deleted=False):
        stamp = (NOW + timedelta(seconds=seconds)).isoformat()
        return await db.client.post(
            "/sync/push",
            headers=db.headers(who),
            json={
                "operations": [
                    {
                        "client_id": str(uid("matrix-client")),
                        "updated_at": stamp,
                        "deleted_at": stamp if deleted else None,
                        "device_id": device,
                        "payload": {"individual_id": "pending", "untrusted": device},
                        "analysis": {"authoritative": False},
                    }
                ]
            },
        )

    for sec, dev, accepted in (
        (-1, "z", False),
        (1, "a", True),
        (1, "0", False),
        (1, "z", True),
    ):
        before = None
        async with db.factory() as session:
            cat = await session.get(RemoteCat, uid("matrix-cat"))
            before = cat.payload
        response = await push("B", sec, dev)
        assert response.status_code == 200
        async with db.factory() as session:
            cat = await session.get(RemoteCat, uid("matrix-cat"))
            assert cat.analysis == {"authoritative": True}
            assert (
                cat.payload.get("untrusted") == dev
                if accepted
                else cat.payload == before
            )
    response = await push("B", 2, "z", True)
    assert response.status_code == 200
    assert (await push("B", 3, "zz")).status_code == 200
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("matrix-cat"))
        assert cat.deleted_at is not None and cat.analysis == {"authoritative": True}
    # Same client ID under another account creates its own row; does not edit B.
    assert (await push("A", 4, "a")).status_code == 200
    async with db.factory() as session:
        rows = (
            await session.scalars(
                select(RemoteCat).where(RemoteCat.client_id == uid("matrix-client"))
            )
        ).all()
        assert len(rows) == 2 and {r.user_id for r in rows} == {uid("A"), uid("B")}
    evidence(
        "DATA05-http",
        {
            "scenario": "DATA-05",
            "dates_and_device_orders": True,
            "tombstone_not_resurrected": True,
            "analysis_preserved": True,
            "same_client_id_isolated": True,
        },
    )


@pytest.mark.asyncio
async def test_DB02_IMG03_reference_filtering(db):
    from app.services.catalog_sync_queries import sync_records

    await seed(db)
    async with db.factory() as session:
        left = await session.scalar(
            select(CatPhoto).where(
                CatPhoto.cat_id == uid("matrix-cat"), CatPhoto.kind == "left"
            )
        )
        left.deleted_at = NOW
        await session.commit()
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("matrix-cat"))
        record = (await sync_records(session, [cat]))[0]
        assert {p.kind for p in record.reference_photos} == {"front", "right", "back"}
        assert record.photo_available and record.analysis == {"authoritative": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["primary", "right"])
async def test_DATA02_upload_failure_records_orphan_without_committing(
    db, monkeypatch, kind
):
    await seed(db)
    before = {p: p.read_bytes() for p in db.storage.root.rglob("*") if p.is_file()}

    async def fail(*args, **kwargs):
        raise RuntimeError("injected after storage save")

    monkeypatch.setattr(sync, "_record_change", fail)
    data = (RUN / "fixtures/synthetic.png").read_bytes() + b"new-body"
    # ASGI propagates this injected application error; the dependency rolls back.
    with pytest.raises(RuntimeError, match="injected"):
        await db.client.put(
            f"/sync/cats/{uid('matrix-client')}/"
            + ("photo" if kind == "primary" else "reference-photos/right"),
            headers=db.headers("B"),
            data={
                "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                "device_id": "b",
            },
            files={"image": ("changed.png", data, "image/png")},
        )
    async with db.factory() as session:
        saved = await session.scalar(
            select(CatPhoto).where(
                CatPhoto.cat_id == uid("matrix-cat"), CatPhoto.kind == kind
            )
        )
        assert (
            saved.content_hash
            == hashlib.sha256((RUN / "fixtures/synthetic.png").read_bytes()).hexdigest()
        )
        assert await session.scalar(select(func.count()).select_from(SyncChange)) == 0
    after = {p: p.read_bytes() for p in db.storage.root.rglob("*") if p.is_file()}
    orphans = set(after) - set(before)
    assert all(after[p] == body for p, body in before.items())
    # Observation, not an acceptance requirement: expose missing compensation honestly.
    evidence(
        "DATA02-file-failure-" + kind,
        {
            "scenario": "DATA-02",
            "database_rolled_back": True,
            "old_files_unchanged": True,
            "new_unreferenced_file_count": len(orphans),
            "orphan_sha256": [
                hashlib.sha256(after[p]).hexdigest() for p in sorted(orphans)
            ],
            "finding": "Inspect cleanup policy if orphan count > 0; no product fix in this stage",
        },
    )


@pytest.mark.asyncio
async def test_DB05_primary_inference_releases_read_connection(db, monkeypatch):
    from app import main

    held = []

    class Analyzer:
        def __init__(self, settings):
            pass

        async def analyze(self, **kwargs):
            held.append(db.engine.sync_engine.pool.checkedout())
            raise main.MissingApiKeyError("controlled unavailable")

    monkeypatch.setattr(main, "AnalysisService", Analyzer)
    response = await db.client.post(
        "/analyze",
        headers=db.headers("A"),
        data={"cat_id": str(uid("matrix-client"))},
        files={
            "image": (
                "synthetic.png",
                (RUN / "fixtures/synthetic.png").read_bytes(),
                "image/png",
            )
        },
    )
    assert response.status_code == 503 and held == [0]
    evidence(
        "DB05-read-release",
        {
            "scenario": "DB-05",
            "connections_during_substituted_analysis": held,
            "status": response.status_code,
        },
    )


@pytest.mark.asyncio
async def test_DATA02_events_wait_for_successful_commit(db, monkeypatch):
    from app.services import chat_commands, realtime_events
    from app.schemas import MessageCreate

    send = AsyncMock()
    monkeypatch.setattr(realtime_events.ws_manager, "send_to_user", send)
    chat = uid("commit-chat")
    async with db.factory() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=uid(w))
                for w in ("A", "B")
            ]
        )
        session.add_all(
            [
                Friendship(user_id=uid(a), friend_id=uid(b))
                for a, b in (("A", "B"), ("B", "A"))
            ]
        )
        await session.commit()
    from app.persistence_models import ConversationMessage

    async with db.factory() as session:
        monkeypatch.setattr(
            session,
            "commit",
            AsyncMock(side_effect=RuntimeError("injected commit failure")),
        )
        with pytest.raises(RuntimeError, match="injected commit failure"):
            await chat_commands.send_message(
                conversation_id=chat,
                payload=MessageCreate(body="synthetic"),
                user=db.users["A"],
                session=session,
            )
        await session.rollback()
    send.assert_not_awaited()
    async with db.factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ConversationMessage))
            == 0
        )
        await chat_commands.send_message(
            conversation_id=chat,
            payload=MessageCreate(body="positive"),
            user=db.users["A"],
            session=session,
        )
    assert send.await_count >= 1
    async with db.factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ConversationMessage))
            == 1
        )
    evidence(
        "DATA02-commit-events",
        {
            "scenario": "DATA-02",
            "failed_commit_messages": 0,
            "failed_commit_publications": 0,
            "successful_commit_positive": True,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["A", "C"])
async def test_SEC03_websocket_loop_malformed_and_membership(db, monkeypatch, actor):
    from fastapi import WebSocketDisconnect
    from app.routers import websocket
    import json

    chat = uid("ws-loop-chat")
    async with db.factory() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=uid(w))
                for w in ("A", "B")
            ]
        )
        session.add_all(
            [
                Friendship(user_id=uid(a), friend_id=uid(b))
                for a, b in (("A", "B"), ("B", "A"), ("C", "A"), ("C", "B"))
            ]
        )
        await session.commit()
    frames = iter(
        [
            "not json",
            "[]",
            "null",
            '{"type":4}',
            '{"type":"typing","conversation_id":"bad"}',
            json.dumps({"type": "typing", "conversation_id": str(chat)}),
            "ping",
        ]
    )

    class Socket:
        def __init__(self):
            self.sent = []

        async def receive_text(self):
            try:
                return next(frames)
            except StopIteration:
                raise WebSocketDisconnect()

        async def send_text(self, value):
            self.sent.append(value)

    socket = Socket()
    send = AsyncMock()
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(websocket, "get_session_factory", lambda: db.factory)
    monkeypatch.setattr(websocket.ws_manager, "connect", connect)
    monkeypatch.setattr(websocket.ws_manager, "disconnect", disconnect)
    monkeypatch.setattr(websocket.ws_manager, "send_to_user", send)
    await websocket.websocket_endpoint(socket, db.tokens[actor])
    assert socket.sent == ["pong"]
    assert send.await_count == (1 if actor == "A" else 0)
    connect.assert_awaited_once()
    disconnect.assert_awaited_once()
    evidence(
        "SEC03-ws-loop-" + actor,
        {
            "scenario": "SEC-03",
            "actor": actor,
            "malformed_frames_ignored": 5,
            "loop_survives": "pong",
            "events": send.await_count,
            "scope": "endpoint coroutine protocol loop; not a TCP WebSocket transport test",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["issuer", "type", "subject", "session", "expired", "signature", "inactive"],
)
async def test_SEC01_http_credential_variants(db, defect):
    import jwt
    from app.config import get_settings

    settings = get_settings()
    assert (await db.client.get("/auth/me", headers=db.headers("A"))).status_code == 200
    payload = jwt.decode(
        db.tokens["A"],
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
    )
    if defect == "issuer":
        payload["iss"] = "untrusted"
    if defect == "type":
        payload["type"] = "refresh"
    if defect == "subject":
        payload["sub"] = str(uid("B"))
    if defect == "session":
        payload["sid"] = str(uid("unknown-session"))
    if defect == "expired":
        payload["exp"] = 1
    if defect == "inactive":
        async with db.factory() as session:
            user = await session.get(type(db.users["A"]), uid("A"))
            user.status = "deactivated"
            await session.commit()
    token = jwt.encode(
        payload,
        "wrong-secret" if defect == "signature" else settings.jwt_secret,
        algorithm="HS256",
    )
    response = await db.client.get(
        "/auth/me", headers={"Authorization": "Bearer " + token}
    )
    assert response.status_code == 401
    evidence(
        "SEC01-" + defect,
        {
            "scenario": "SEC-01",
            "defect": defect,
            "positive_status": 200,
            "negative_status": response.status_code,
        },
    )


@pytest.fixture
def mailpit():
    import subprocess
    import time
    import urllib.request
    from uuid import uuid4
    import json

    name = "gatalogo-campaign-mail-" + uuid4().hex[:12]
    image = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", "gatalogo-mailpit"], text=True
    ).strip()
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--label",
            "gatalogo.purpose=quality-mail",
            "-p",
            "127.0.0.1::1025",
            "-p",
            "127.0.0.1::8025",
            image,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:

        def port(number):
            return int(
                subprocess.check_output(
                    ["docker", "port", name, str(number) + "/tcp"], text=True
                )
                .strip()
                .rsplit(":", 1)[1]
            )

        smtp_port, http_port = port(1025), port(8025)
        url = "http://127.0.0.1:" + str(http_port)

        def get(path):
            with urllib.request.urlopen(url + path, timeout=3) as response:
                return json.load(response)

        for _ in range(100):
            try:
                get("/api/v1/messages")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Disposable Mailpit not ready")
        yield {"port": smtp_port, "get": get, "image": image}
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name], check=True, stdout=subprocess.DEVNULL
        )


@pytest.mark.asyncio
async def test_DLV01_disposable_smtp_registration_and_consumption(
    db, mailpit, monkeypatch
):
    from app.config import get_settings
    import re

    settings = get_settings()
    for key, value in {
        "smtp_enabled": True,
        "smtp_host": "127.0.0.1",
        "smtp_port": mailpit["port"],
        "smtp_use_tls": False,
        "smtp_username": None,
        "smtp_password": None,
        "smtp_from": "campaign@example.com",
    }.items():
        monkeypatch.setattr(settings, key, value)
    recipient = "registration@example.com"
    response = await db.client.post(
        "/auth/register",
        json={"email": recipient, "password": "Synthetic_password_2026!"},
    )
    assert response.status_code == 202
    messages = mailpit["get"]("/api/v1/messages")["messages"]
    assert len(messages) == 1
    content = mailpit["get"]("/api/v1/message/" + messages[0]["ID"])
    assert any(m["Address"] == recipient for m in content["To"])
    codes = re.findall(r"\b[0-9]{6}\b", content["Text"])
    assert len(codes) == 1
    verify = await db.client.post(
        "/auth/verify-email",
        json={"email": recipient, "code": codes[0], "device_id": "campaign"},
    )
    assert verify.status_code == 200
    assert (
        await db.client.get(
            "/auth/me",
            headers={"Authorization": "Bearer " + verify.json()["access_token"]},
        )
    ).status_code == 200
    again = await db.client.post(
        "/auth/verify-email", json={"email": recipient, "code": codes[0]}
    )
    assert again.status_code == 400
    evidence(
        "DLV01-local-smtp",
        {
            "scenario": "DLV-01",
            "mailpit_image": mailpit["image"],
            "smtp_messages": 1,
            "recipient_matches": True,
            "otp_used_once": True,
            "scope": "Actual SMTP capture in disposable Mailpit, no external email delivery. Code and tokens omitted.",
        },
    )


@pytest.mark.asyncio
async def test_DLV02_smtp_failure_does_not_persist_otp(db, monkeypatch):
    from app.services import auth_service
    from app.config import Settings
    import smtplib

    class Rejected:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise smtplib.SMTPConnectError(421, "controlled unavailable")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(auth_service.smtplib, "SMTP", Rejected)
    settings = Settings(
        _env_file=None,
        smtp_enabled=True,
        smtp_host="127.0.0.1",
        smtp_port=1,
        smtp_use_tls=False,
    )
    async with db.factory() as session:
        with pytest.raises(smtplib.SMTPConnectError):
            await auth_service.create_otp(
                session,
                email="a@example.com",
                purpose="smtp_failure",
                user_id=uid("A"),
                settings=settings,
            )
        await session.rollback()
    from app.persistence_models import EmailOtp

    async with db.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EmailOtp)
                .where(EmailOtp.purpose == "smtp_failure")
            )
            == 0
        )
    evidence(
        "DLV02-refusal",
        {
            "scenario": "DLV-02",
            "failure": "simulated SMTP refusal before acceptance",
            "persisted_otps": 0,
            "scope": "No slow/drop-after-acceptance experiment; those variants pending",
        },
    )


@pytest.mark.asyncio
async def test_SEC10_open_websocket_revocation_window_observation(db, monkeypatch):
    from datetime import datetime, UTC
    from fastapi import WebSocketDisconnect
    from app.routers import websocket
    from app.persistence_models import UserSession
    from app.config import get_settings
    import jwt, json

    sid = jwt.decode(
        db.tokens["A"],
        get_settings().jwt_secret,
        algorithms=["HS256"],
        issuer=get_settings().jwt_issuer,
    )["sid"]
    from uuid import UUID

    chat = uid("revocation-chat")
    async with db.factory() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=uid(w))
                for w in ("A", "B")
            ]
        )
        session.add_all(
            [
                Friendship(user_id=uid(a), friend_id=uid(b))
                for a, b in (("A", "B"), ("B", "A"))
            ]
        )
        await session.commit()

    class Socket:
        def __init__(self):
            self.step = 0
            self.sent = []
            self.closed = []

        async def receive_text(self):
            self.step += 1
            if self.step == 1:
                return "ping"
            if self.step == 2:
                async with db.factory() as session:
                    active = await session.get(UserSession, UUID(sid))
                    active.revoked_at = datetime.now(UTC)
                    await session.commit()
                return json.dumps({"type": "typing", "conversation_id": str(chat)})
            raise WebSocketDisconnect()

        async def send_text(self, value):
            self.sent.append(value)

        async def close(self, code):
            self.closed.append(code)

    send = AsyncMock()
    monkeypatch.setattr(websocket, "get_session_factory", lambda: db.factory)
    monkeypatch.setattr(websocket.ws_manager, "connect", AsyncMock())
    monkeypatch.setattr(websocket.ws_manager, "disconnect", AsyncMock())
    monkeypatch.setattr(websocket.ws_manager, "send_to_user", send)
    opened = Socket()
    await websocket.websocket_endpoint(opened, db.tokens["A"])
    http_status = (await db.client.get("/auth/me", headers=db.headers("A"))).status_code
    assert http_status == 401
    newly_opened = Socket()
    await websocket.websocket_endpoint(newly_opened, db.tokens["A"])
    assert newly_opened.closed == [1008]
    evidence(
        "SEC10-open-revocation",
        {
            "scenario": "SEC-10",
            "http_after_revocation": http_status,
            "new_connection_close": 1008,
            "events_from_previously_open_session_after_revocation": send.await_count,
            "finding": "Existing socket loop does not revalidate revocation before conversation action"
            if send.await_count
            else None,
            "scope": "Real endpoint coroutine with synthetic transport. No elapsed-time bound or actual TCP measurement.",
        },
    )


@pytest.mark.asyncio
async def test_SEC06_bounded_upload_and_schema_limits(db, monkeypatch):
    from app.config import get_settings

    await seed(db)
    monkeypatch.setattr(get_settings(), "max_image_mb", 1)
    path = f"/sync/cats/{uid('matrix-client')}/reference-photos/front"
    too_big = await db.client.put(
        path,
        headers=db.headers("B"),
        files={
            "image": (
                "synthetic.png",
                b"x" * (get_settings().max_image_bytes + 1),
                "image/png",
            )
        },
    )
    assert too_big.status_code == 413
    assert (await db.client.put(path, headers=db.headers("B"))).status_code == 422
    assert (
        await db.client.get("/sync/pull?limit=501", headers=db.headers("A"))
    ).status_code == 422
    assert (
        await db.client.get("/sync/pull?cursor=-1", headers=db.headers("A"))
    ).status_code == 422
    operation = {
        "client_id": str(uid("large-batch")),
        "updated_at": NOW.isoformat(),
        "device_id": "bounded",
    }
    assert (
        await db.client.post(
            "/sync/push",
            headers=db.headers("A"),
            json={"operations": [operation] * 501},
        )
    ).status_code == 422
    async with db.factory() as session:
        assert await session.scalar(select(func.count()).select_from(SyncChange)) == 0
    evidence(
        "SEC06-bounds",
        {
            "scenario": "SEC-06",
            "test_image_limit_bytes": 1048576,
            "over_limit": 413,
            "missing_multipart": 422,
            "out_of_bounds_query": 422,
            "push_501": 422,
            "sensitive_changes": 0,
            "scope": "Limits lowered only in isolated settings; no huge requests or pixel decompression test",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [2, 10, 25])
async def test_LOAD03_same_intent_bounded_barrier(db, count):
    import asyncio
    from app.services import progression
    from app.services.quintal_production import operation
    from app.persistence_models import HabitatTransaction, HabitatWallet
    from app.quintal_models import QuintalTask

    barrier = asyncio.Event()

    async def call():
        await barrier.wait()
        async with db.factory() as session:
            await progression.account(session, uid("A"))
            result = await operation(
                session,
                uid("A"),
                "order",
                "chicken",
                {"payment": "coins"},
                uid("bounded-intent"),
                now=NOW,
            )
            await session.commit()
            return result

    calls = [asyncio.create_task(call()) for _ in range(count)]
    barrier.set()
    results = await asyncio.gather(*calls)
    assert all(result == results[0] for result in results)
    async with db.factory() as session:
        assert (await session.get(HabitatWallet, uid("A"))).balance == 88
        assert (
            await session.scalar(
                select(func.count())
                .select_from(HabitatTransaction)
                .where(
                    HabitatTransaction.user_id == uid("A"),
                    HabitatTransaction.event_key
                    == "quintal:" + str(uid("bounded-intent")),
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(QuintalTask)
                .where(QuintalTask.user_id == uid("A"))
            )
            == 1
        )
    evidence(
        "LOAD03-same-intent-" + str(count),
        {
            "scenario": "LOAD-03",
            "concurrent_calls": count,
            "intents": 1,
            "wallet_balance": 88,
            "ledger_receipts": 1,
            "tasks": 1,
            "scope": "Short functional barrier, not a capacity/throughput or prolonged load experiment",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 10, 50])
@pytest.mark.parametrize("consumers", [1, 2])
async def test_LOAD04_controlled_reference_queue(db, monkeypatch, count, consumers):
    import asyncio
    from collections import Counter
    from app.services import reference_analysis as worker
    from app.services.vision_jobs import enqueue_reference_analysis

    for name in (
        "confirm_photo",
        "rebuild_analysis_from_photos",
        "publish_committed_events",
    ):
        monkeypatch.setattr(worker, name, AsyncMock())
    data = (RUN / "fixtures/synthetic.png").read_bytes()
    async with db.factory() as session:
        for i in range(count):
            cat = RemoteCat(
                id=uid("queue-cat-" + str(i)),
                client_id=uid("queue-client-" + str(i)),
                user_id=uid("A"),
                payload={},
                client_updated_at=NOW,
                updated_by_device="queue",
            )
            session.add(cat)
            await session.flush()
            key, digest = db.storage.save(
                user_id=uid("A"), cat_id=cat.id, data=data, suffix=".png"
            )
            photo = CatPhoto(
                cat_id=cat.id,
                kind="front",
                storage_key=key,
                content_hash=digest,
                status="analyzing",
            )
            session.add(photo)
            await session.flush()
            await enqueue_reference_analysis(session, cat=cat, photo=photo)
        await session.commit()
    calls = []
    pool = []

    async def analyze(**kwargs):
        calls.append(kwargs["cat_id"])
        pool.append(db.engine.sync_engine.pool.checkedout())
        assert kwargs["image_bytes"] == data
        await asyncio.sleep(0)
        return {"synthetic": True}

    async def consume():
        for _ in range(count * 2 + 5):
            if not await worker.process_next(db.factory, analyzer=analyze):
                return
        raise AssertionError("Worker did not drain bounded queue")

    await asyncio.gather(*[consume() for _ in range(consumers)])
    assert len(calls) == count and all(v == 1 for v in Counter(calls).values())
    async with db.factory() as session:
        jobs = (await session.scalars(select(VisionJob))).all()
        assert len(jobs) == count and all(
            j.status == "completed" and j.attempts == 1 for j in jobs
        )
        photos = (await session.scalars(select(CatPhoto))).all()
        assert all(
            p.status == "ready" and p.analysis == {"synthetic": True} for p in photos
        )
    if consumers == 1:
        assert pool == [0] * count
    evidence(
        "LOAD04-queue-" + str(count) + "-" + str(consumers),
        {
            "scenarios": ["LOAD-04", "DB-05"],
            "jobs": count,
            "consumers": consumers,
            "completed": len(calls),
            "duplicate_inference": False,
            "pool_checked_out_during_fake_inference": pool,
            "scope": "Synthetic fixed analyzer; with2 consumers global pool can belong to the other consumer. No real AI/capacity claim.",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 10, 50])
async def test_LOAD05_manager_fanout_and_cleanup(db, count):
    from app.services.websocket_manager import ConnectionManager

    manager = ConnectionManager()

    class Socket:
        def __init__(self):
            self.received = []

        async def accept(self):
            pass

        async def send_text(self, message):
            self.received.append(message)

    sockets = [Socket() for _ in range(count)]
    for socket in sockets:
        await manager.connect(uid("A"), socket)
    await manager.send_to_user(uid("A"), "synthetic", {"sequence": 1})
    assert all(len(s.received) == 1 for s in sockets)
    for socket in sockets:
        await manager.disconnect(uid("A"), socket)
    assert not manager.is_online(uid("A"))
    await manager.send_to_user(uid("A"), "synthetic", {"sequence": 2})
    assert all(len(s.received) == 1 for s in sockets)
    evidence(
        "LOAD05-manager-" + str(count),
        {
            "scenario": "LOAD-05",
            "synthetic_sockets": count,
            "one_event_each": True,
            "online_after_disconnect": False,
            "scope": "Manager component; no TCP sockets, slow reader, reconnect or WS latency evidence",
        },
    )


@pytest.mark.asyncio
async def test_DLV02_smtp_accepted_before_database_commit_failure(
    db, mailpit, monkeypatch
):
    from app.services import auth_service
    from app.config import Settings
    from app.persistence_models import EmailOtp

    settings = Settings(
        _env_file=None,
        smtp_enabled=True,
        smtp_host="127.0.0.1",
        smtp_port=mailpit["port"],
        smtp_use_tls=False,
        smtp_from="campaign@example.com",
    )
    async with db.factory() as session:
        await auth_service.create_otp(
            session,
            email="a@example.com",
            purpose="post_smtp_failure",
            user_id=uid("A"),
            settings=settings,
        )
        monkeypatch.setattr(
            session,
            "commit",
            AsyncMock(
                side_effect=RuntimeError("injected commit after SMTP acceptance")
            ),
        )
        with pytest.raises(RuntimeError, match="injected commit"):
            await session.commit()
        await session.rollback()
    async with db.factory() as session:
        stored = await session.scalar(
            select(func.count())
            .select_from(EmailOtp)
            .where(EmailOtp.purpose == "post_smtp_failure")
        )
    messages = mailpit["get"]("/api/v1/messages")["messages"]
    assert stored == 0 and len(messages) == 1
    evidence(
        "DLV02-smtp-before-commit",
        {
            "scenario": "DLV-02",
            "smtp_accepted_messages": 1,
            "persisted_otps": 0,
            "finding": "SMTP acceptance precedes the caller commit; a commit failure can leave a delivered but unusable code",
            "scope": "Actual disposable SMTP with injected DB commit failure; code omitted, no external email sent",
        },
    )


@pytest.mark.asyncio
async def test_SEC02_SEC08_export_and_favorites_owner_isolation(db, caplog):
    await seed(db)
    async with db.factory() as session:
        cat = RemoteCat(
            user_id=uid("A"),
            client_id=uid("export-A"),
            payload={"name": "EXPORT_A_CANARY"},
            client_updated_at=NOW,
            updated_by_device="a",
        )
        session.add(cat)
        other = await session.get(RemoteCat, uid("matrix-cat"))
        other.payload = {**other.payload, "name": "EXPORT_B_PRIVATE_CANARY"}
        await session.commit()
    for who in ("A", "B"):
        response = await db.client.put(
            "/app/favorites",
            headers=db.headers(who),
            json={"favorites": [who + "-favorite"], "user_id": str(uid("B"))},
        )
        assert response.status_code == 200
    for who in ("A", "B"):
        response = await db.client.get("/app/favorites", headers=db.headers(who))
        assert response.status_code == 200 and response.json()["favorites"] == [
            who + "-favorite"
        ]
    exported = await db.client.get("/app/account/export", headers=db.headers("A"))
    assert exported.status_code == 200
    assert [c["name"] for c in exported.json()["cats"]] == ["EXPORT_A_CANARY"]
    assert "EXPORT_B_PRIVATE_CANARY" not in exported.text
    invalid = await db.client.get(
        "/auth/me", headers={"Authorization": "Bearer TOKEN_CANARY_INVALID"}
    )
    assert invalid.status_code == 401 and "TOKEN_CANARY_INVALID" not in invalid.text
    assert (await db.client.get("/app/account/export")).status_code == 401
    assert "TOKEN_CANARY_INVALID" not in caplog.text
    assert all(token not in caplog.text for token in db.tokens.values())
    evidence(
        "SEC08-owner-export",
        {
            "scenarios": ["SEC-02", "SEC-08"],
            "own_export_positive": True,
            "foreign_canary_excluded": True,
            "favorite_owner_keys_isolated": True,
            "auth_canary_not_in_error_or_captured_logs": True,
            "scope": "Selected HTTP/export/log paths only; not all auth/upload/IA logging or full exported-data inventory",
        },
    )


@pytest.mark.asyncio
async def test_SEC08_export_owned_missing_report_contract(db):
    from app.persistence_models import MissingReport

    await seed(db)
    async with db.factory() as session:
        session.add(
            MissingReport(
                id=uid("export-report"),
                user_id=uid("B"),
                cat_id=uid("matrix-cat"),
                latitude=-23,
                longitude=-46,
                radius_meters=500,
                status="active",
            )
        )
        await session.commit()
    try:
        response = await db.client.get("/app/account/export", headers=db.headers("B"))
    except AttributeError as error:
        evidence(
            "SEC08-export-report-failure",
            {
                "scenario": "SEC-08",
                "own_reports": 1,
                "observed": "unhandled AttributeError",
                "message": str(error),
                "scope": "Actual HTTP export against persisted real MissingReport; no token or response dump",
            },
        )
        raise
    evidence(
        "SEC08-export-report",
        {"scenario": "SEC-08", "own_reports": 1, "status": response.status_code},
    )
    assert response.status_code == 200
    assert len(response.json()["missing_reports"]) == 1


@pytest.mark.asyncio
async def test_SEC02_SEC04_clue_location_and_media_permissions(db):
    from app.persistence_models import MissingReport, Sighting, MatchSuggestion
    from PIL import Image
    from io import BytesIO

    await seed(db)
    image = Image.new("RGB", (16, 32), "white")
    metadata = Image.Exif()
    metadata[270] = "PRIVATE_EXIF_CANARY"
    metadata[274] = 6
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=metadata)
    data = buffer.getvalue()
    key, digest = db.storage.save(
        user_id=uid("A"), cat_id=uid("clue-candidate"), data=data, suffix=".jpg"
    )
    async with db.factory() as session:
        candidate = RemoteCat(
            id=uid("clue-candidate"),
            client_id=uid("clue-client"),
            user_id=uid("A"),
            payload={},
            client_updated_at=NOW,
            updated_by_device="a",
            photo_key=key,
            photo_hash=digest,
            photo_content_type="image/jpeg",
        )
        session.add(candidate)
        await session.flush()
        report = MissingReport(
            id=uid("clue-report"),
            user_id=uid("B"),
            cat_id=uid("matrix-cat"),
            latitude=-23,
            longitude=-46,
            radius_meters=500,
            status="active",
        )
        sighting = Sighting(
            id=uid("clue-sighting"),
            observer_id=uid("A"),
            cat_id=candidate.id,
            latitude=-23.123456,
            longitude=-46.123456,
            note="synthetic public observation",
        )
        session.add_all([report, sighting])
        await session.flush()
        session.add(
            MatchSuggestion(
                id=uid("private-clue"),
                missing_report_id=report.id,
                sighting_id=sighting.id,
                similarity=0.9,
                status="pending",
            )
        )
        await session.commit()
    path = f"/app/reports/{uid('clue-report')}/clues"
    photo = f"/app/clues/{uid('private-clue')}/photo"
    for who in ("A", "C"):
        assert (await db.client.get(path, headers=db.headers(who))).status_code == 404
        assert (await db.client.get(photo, headers=db.headers(who))).status_code == 404
    listed = await db.client.get(path, headers=db.headers("B"))
    assert listed.status_code == 200
    clue = listed.json()[0]
    assert (
        clue["latitude"] is None
        and clue["longitude"] is None
        and clue["status"] == "pending"
    )
    response = await db.client.get(photo, headers=db.headers("B"))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(response.content)) as thumbnail:
        assert thumbnail.size == (32, 16) and not thumbnail.getexif()
    assert b"PRIVATE_EXIF_CANARY" not in response.content
    async with db.factory() as session:
        clue_match = await session.get(MatchSuggestion, uid("private-clue"))
        clue_match.location_revealed_at = NOW
        await session.commit()
    revealed = (await db.client.get(path, headers=db.headers("B"))).json()[0]
    assert revealed["latitude"] == -23.123456 and revealed["longitude"] == -46.123456
    async with db.factory() as session:
        clue_match = await session.get(MatchSuggestion, uid("private-clue"))
        clue_match.status = "stale"
        await session.commit()
    assert (await db.client.get(path, headers=db.headers("B"))).json() == []
    assert (await db.client.get(photo, headers=db.headers("B"))).status_code == 404
    evidence(
        "SEC04-clue-media",
        {
            "scenarios": ["SEC-02", "SEC-04"],
            "report_owner_media_positive": True,
            "observer_and_stranger_denied": True,
            "coordinates_hidden_until_revealed": True,
            "exif_description_removed": True,
            "orientation_applied": True,
            "stale_media_denied": True,
            "scope": "Seeded suggestion for permission tests only; similarity is not reidentification evidence or a confirmed identity",
        },
    )
