"""Critical deterministic cases, imported only against a frozen disposable target."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
ATTEMPT = Path(os.environ["QUALITY_CAMPAIGN_ATTEMPT"])
SOURCE = Path(os.environ.get("QUALITY_TARGET_SOURCE", RUN / "sources" / "B" / "api")).resolve()
assert SOURCE.is_relative_to(RUN / "sources")
sys.path.insert(0, str(SOURCE))

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app import main
from app.config import get_settings
from app.database import Base, get_database_session
from app.persistence_models import (
    CatPhoto, Conversation, ConversationMember, ConversationMessage, EmailOtp,
    Friendship, HabitatTransaction, HabitatWallet, RemoteCat, SyncChange, User, VisionJob,
)
from app.quintal_models import QuintalFacility, QuintalResource, QuintalTask
from app.routers import websocket
from app.routers.sync import _incoming_wins, pull
from app.schemas import AuthTokens, SyncOperation
from app.services import auth_service, progression
from app.services.auth_service import issue_tokens
from app.services.fcm_service import fcm_sender
from app.services.quintal_production import operation
from app.services.storage_service import PrivatePhotoStorage
from fixtures import NOW, uid


def evidence(test, value):
    directory = Path(os.environ.get("QUALITY_CASE_EVIDENCE", ATTEMPT / "critical"))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{test}.json"
    assert not path.exists(), "Never overwrite case evidence"
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


@pytest_asyncio.fixture
async def db(request, tmp_path, monkeypatch):
    url = os.environ["RECOGNITION_TEST_DATABASE_URL"]
    assert url.endswith("/recognition_test") and ":5432/" not in url
    schema = "critical_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema + ",public"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        async with engine.begin() as conn:
            assert await conn.scalar(text("SELECT current_schema()")) == schema
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        settings = get_settings()
        monkeypatch.setattr(settings, "habitat_free_shop", False)
        monkeypatch.setattr(fcm_sender, "send_notification", AsyncMock(return_value={"delivered": [], "retry": False}))
        storage = PrivatePhotoStorage(tmp_path / "private")
        from app.services.storage_service import photo_storage
        monkeypatch.setattr(photo_storage, "root", storage.root)
        tokens, users = {}, {}
        async with factory() as session:
            for who in ("A", "B", "C"):
                user = User(id=uid(who), email=f"{who.lower()}@example.com", status="active")
                users[who] = user
                session.add(user)
                await session.flush()
                bundle = await issue_tokens(session, user=user, device_id="synthetic")
                AuthTokens.model_validate(bundle)  # fixture must satisfy the actual HTTP response contract
                tokens[who] = bundle["access_token"]
                session.add(HabitatWallet(user_id=user.id, balance=100, care_state={}, selected_cat_ids=[], expansion_level=0))
                session.add(QuintalFacility(user_id=user.id, kind="market", level=1, target_level=0))
            await session.commit()

        async def database():
            async with factory() as session:
                try:
                    yield session
                finally:
                    await session.rollback()

        main.app.dependency_overrides[get_database_session] = database
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://critical") as client:
            yield SimpleNamespace(engine=engine, factory=factory, storage=storage, users=users, tokens=tokens,
                                  client=client, headers=lambda who: {"Authorization": "Bearer " + tokens[who]})
    finally:
        main.app.dependency_overrides.pop(get_database_session, None)
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["primary", "front"])
async def test_SEC02_private_photo_positive_control(db, kind):
    data = (RUN / "fixtures" / "synthetic.png").read_bytes()
    async with db.factory() as session:
        cat = RemoteCat(id=uid("private-cat"), client_id=uid("private-client"), user_id=uid("B"),
                        payload={}, client_updated_at=NOW, updated_by_device="b1")
        key, digest = db.storage.save(user_id=uid("B"), cat_id=cat.id, data=data, suffix=".png")
        cat.photo_key, cat.photo_hash, cat.photo_content_type = key, digest, "image/png"
        session.add(cat)
        await session.flush()
        session.add(CatPhoto(cat_id=cat.id, kind="front", storage_key=key, content_hash=digest, content_type="image/png", status="ready"))
        await session.commit()
    suffix = "photo" if kind == "primary" else "reference-photos/front"
    path = f'/sync/cats/{uid("private-client")}/{suffix}'
    statuses = {}
    for actor, expected in (("B", 200), ("A", 404), ("C", 404), (None, 401)):
        reply = await db.client.get(path, headers=db.headers(actor) if actor else {})
        statuses[actor or "anonymous"] = reply.status_code
        assert reply.status_code == expected
        if actor == "B":
            assert reply.content == data
            assert reply.headers["content-type"].startswith("image/png")
        else:
            assert reply.content != data
    evidence(f"SEC02-{kind}", {"scenario": "SEC-02", "actor_status": statuses,
                              "owner_content_sha256": hashlib.sha256(data).hexdigest(), "positive_control": True})


@pytest.mark.asyncio
async def test_SEC02_pull_filters_both_owners(db):
    async with db.factory() as session:
        for who in ("A", "B"):
            cat = RemoteCat(id=uid("owned-" + who), client_id=uid("client-" + who), user_id=uid(who),
                            client_updated_at=NOW, updated_by_device=who, payload={"private": who})
            session.add(cat)
            await session.flush()
            session.add(SyncChange(user_id=uid(who), cat_id=cat.id))
        await session.commit()
    for who in ("A", "B"):
        reply = await db.client.get("/sync/pull", headers=db.headers(who))
        assert reply.status_code == 200
        assert [r["client_id"] for r in reply.json()["changes"]] == [str(uid("client-" + who))]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["typing", "read"])
async def test_SEC03_stranger_friend_cannot_emit_or_mark_read(db, monkeypatch, kind):
    chat, message = uid("chat"), uid("message")
    async with db.factory() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all([ConversationMember(conversation_id=chat, user_id=uid(w)) for w in ("A", "B")])
        # C knows both participants. Friendship alone must not grant membership.
        session.add_all([Friendship(user_id=uid(a), friend_id=uid(b)) for a, b in
                         (("A", "B"), ("B", "A"), ("C", "A"), ("C", "B"))])
        session.add(ConversationMessage(id=message, conversation_id=chat, sender_id=uid("A"), body="synthetic"))
        await session.commit()
    send = AsyncMock()
    monkeypatch.setattr(websocket.ws_manager, "send_to_user", send)
    # Positive control for allowed typing, then isolate the unauthorized attempt.
    await websocket._conversation_event(db.factory, db.users["B"], {"type": "typing", "conversation_id": str(chat)})
    send.assert_awaited_once()
    send.reset_mock()
    with pytest.raises(HTTPException) as error:
        await websocket._conversation_event(db.factory, db.users["C"], {"type": kind, "conversation_id": str(chat)})
    assert error.value.status_code == 403
    send.assert_not_awaited()
    async with db.factory() as session:
        assert (await session.get(ConversationMessage, message)).read_at is None
    evidence(f"SEC03-{kind}", {"scenario": "SEC-03", "positive_member_control": True,
                              "denied_status": 403, "unauthorized_events": 0, "read_at": None})


@pytest.mark.parametrize("key", ["../sentinel.txt", "..\\sentinel.txt", "users/../../sentinel.txt", "absolute"])
def test_SEC05_storage_escape_never_reads_or_deletes(tmp_path, key):
    root = tmp_path / "private"
    root.mkdir()
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    if key == "absolute":
        key = str(sentinel.resolve())
    storage = PrivatePhotoStorage(root)
    safe, _ = storage.save(user_id=uid("A"), cat_id=uid("safe"), data=b"safe", suffix=".jpg")
    assert storage.open(safe).read_bytes() == b"safe"
    for call in (storage.absolute_path, storage.open, storage.delete):
        with pytest.raises(ValueError):
            call(key)
    assert sentinel.read_text(encoding="utf-8") == "untouched"


async def otp(db, name="case", *, expires=None, attempts=0):
    async with db.factory() as session:
        row = EmailOtp(id=uid("otp-" + name), user_id=uid("A"), email="a@example.com", purpose=name,
                       code_hash=auth_service.hash_otp(code="123456", email="a@example.com", purpose=name, settings=get_settings()),
                       expires_at=expires or NOW + timedelta(minutes=5), attempts=attempts)
        session.add(row)
        await session.commit()
    return row.id


@pytest.mark.asyncio
async def test_SEC07_failed_attempts_survive_request_rollback(db, monkeypatch):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    ident = await otp(db)
    limit = get_settings().otp_max_attempts
    for _ in range(limit):
        async with db.factory() as session:
            with pytest.raises(HTTPException):
                await auth_service.consume_otp(session, email="a@example.com", purpose="case", code="000000")
            await session.rollback()
    async with db.factory() as session:
        stored = await session.get(EmailOtp, ident)
        assert stored.attempts == limit
        with pytest.raises(HTTPException):
            await auth_service.consume_otp(session, email=stored.email, purpose="case", code="123456")
    evidence("SEC07-attempts", {"scenario": "SEC-07", "attempts_persisted": limit, "valid_code_after_limit": "rejected"})


@pytest.mark.asyncio
async def test_SEC07_expiry_purpose_email_and_reuse(db, monkeypatch):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    await otp(db, "expired", expires=NOW)
    await otp(db, "valid")
    for email, purpose in (("a@example.com", "expired"), ("b@example.com", "valid"), ("a@example.com", "wrong-purpose")):
        async with db.factory() as session:
            with pytest.raises(HTTPException):
                await auth_service.consume_otp(session, email=email, purpose=purpose, code="123456")
    async with db.factory() as session:
        row = await auth_service.consume_otp(session, email="a@example.com", purpose="valid", code="123456")
        assert row.consumed_at == NOW
        await session.commit()
    async with db.factory() as session:
        with pytest.raises(HTTPException):
            await auth_service.consume_otp(session, email="a@example.com", purpose="valid", code="123456")


@pytest.mark.asyncio
async def test_SEC07_resend_cooldown_invalidates_previous(db, monkeypatch):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    sender = AsyncMock()
    monkeypatch.setattr(auth_service, "_send_otp", sender)
    async with db.factory() as session:
        await auth_service.create_otp(session, email="a@example.com", purpose="resend", user_id=uid("A"))
        rows = list((await session.scalars(select(EmailOtp))).all())
        rows[0].created_at = NOW
        await session.commit()
    async with db.factory() as session:
        with pytest.raises(HTTPException) as rejected:
            await auth_service.create_otp(session, email="a@example.com", purpose="resend")
        assert rejected.value.status_code == 429
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW+timedelta(seconds=61))
    async with db.factory() as session:
        await auth_service.create_otp(session, email="a@example.com", purpose="resend", user_id=uid("A"))
        await session.commit()
        rows = list((await session.scalars(select(EmailOtp))).all())
        assert len(rows) == 2 and sum(row.consumed_at is None for row in rows) == 1
    assert sender.await_count == 2


@pytest.mark.asyncio
async def test_SEC07_refresh_and_logout_revoke_previous_credentials(db):
    async with db.factory() as session:
        bundle = await issue_tokens(session, user=db.users["A"], device_id="rotation")
        await session.commit()
    rotated = await db.client.post("/auth/refresh", json={"refresh_token": bundle["refresh_token"], "device_id": "rotation"})
    assert rotated.status_code == 200
    fresh = rotated.json()
    assert fresh["refresh_token"] != bundle["refresh_token"]
    old_access = await db.client.get("/sync/pull", headers={"Authorization": "Bearer " + bundle["access_token"]})
    assert old_access.status_code == 401
    old_refresh = await db.client.post("/auth/refresh", json={"refresh_token": bundle["refresh_token"], "device_id": "rotation"})
    assert old_refresh.status_code == 401
    headers = {"Authorization": "Bearer " + fresh["access_token"]}
    assert (await db.client.get("/sync/pull", headers=headers)).status_code == 200
    assert (await db.client.post("/auth/logout", headers=headers)).status_code == 204
    assert (await db.client.get("/sync/pull", headers=headers)).status_code == 401
    assert (await db.client.post("/auth/refresh", json={"refresh_token": fresh["refresh_token"]})).status_code == 401
    evidence("SEC07-refresh", {"scenario": "SEC-07", "rotation": "new credentials", "old_access": 401,
                              "old_refresh": 401, "post_logout_access": 401, "post_logout_refresh": 401})


async def reference_claim(db, monkeypatch):
    from app.services import reference_analysis as worker
    from app.services.vision_jobs import enqueue_reference_analysis
    for name in ("confirm_photo", "rebuild_analysis_from_photos", "publish_committed_events"):
        monkeypatch.setattr(worker, name, AsyncMock())
    data = (RUN / "fixtures" / "synthetic.png").read_bytes()
    async with db.factory() as session:
        cat = RemoteCat(id=uid("fence-cat"), user_id=uid("A"), client_id=uid("fence-client"),
                        payload={}, client_updated_at=NOW, updated_by_device="a")
        key, digest = db.storage.save(user_id=uid("A"), cat_id=cat.id, data=data, suffix=".png")
        photo = CatPhoto(id=uid("fence-photo"), cat_id=cat.id, kind="front", storage_key=key,
                         content_hash=digest, status="analyzing")
        session.add(cat)
        await session.flush()
        session.add(photo)
        await session.flush()
        await enqueue_reference_analysis(session, cat=cat, photo=photo)
        await session.commit()
    claim = await worker._claim(db.factory, get_settings())
    assert claim is not None
    return worker, claim


@pytest.mark.asyncio
async def test_DATA06_old_finish_while_new_attempt_is_running(db, monkeypatch):
    worker, old = await reference_claim(db, monkeypatch)
    settings = get_settings()
    async with db.factory() as session:
        job = await session.get(VisionJob, old.job_id)
        job.started_at = datetime.now(UTC)-timedelta(seconds=settings.vision_job_lease_seconds+1)
        await session.commit()
    current = await worker._claim(db.factory, settings)
    assert current.attempt == old.attempt + 1
    await worker._finish(db.factory, old, settings, incoming={"marker": "obsolete"})
    async with db.factory() as session:
        assert (await session.get(CatPhoto, old.photo_id)).analysis is None
        assert (await session.get(VisionJob, old.job_id)).status == "running"
    worker.confirm_photo.assert_not_awaited()
    await worker._finish(db.factory, current, settings, incoming={"marker": "current"})
    async with db.factory() as session:
        assert (await session.get(CatPhoto, old.photo_id)).analysis == {"marker": "current"}
    worker.confirm_photo.assert_awaited_once()
    evidence("DATA06-attempt-fence", {"scenario": "DATA-06", "old_attempt": old.attempt,
                                     "new_attempt": current.attempt, "old_finish_during_new_running": "discarded"})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cat-delete", "photo-delete", "replace-hash"])
async def test_DATA06_delete_or_replace_between_claim_and_finish(db, monkeypatch, change):
    worker, claim = await reference_claim(db, monkeypatch)
    async with db.factory() as session:
        cat, photo = await session.get(RemoteCat, claim.cat_id), await session.get(CatPhoto, claim.photo_id)
        if change == "cat-delete":
            cat.deleted_at = NOW
        elif change == "photo-delete":
            photo.deleted_at = NOW
        else:
            photo.content_hash = "b" * 64
        await session.commit()
    await worker._finish(db.factory, claim, get_settings(), incoming={"marker": "obsolete"})
    async with db.factory() as session:
        assert (await session.get(CatPhoto, claim.photo_id)).analysis is None
        assert (await session.get(VisionJob, claim.job_id)).status == "superseded"
    worker.confirm_photo.assert_not_awaited()
    evidence("DATA06-"+change, {"scenario": "DATA-06", "interleaving": change, "result": "superseded"})


@pytest.mark.parametrize("offset,device,deleted,expected", [
    (-1, "z", False, False), (1, "a", False, True), (0, "z", False, True),
    (0, "a", False, False), (1, "z", True, False),
])
def test_DATA05_conflict_and_tombstone(offset, device, deleted, expected):
    cat = RemoteCat(client_updated_at=NOW, updated_by_device="m", deleted_at=NOW if deleted else None)
    incoming = SyncOperation(client_id=uid("sync"), updated_at=NOW + timedelta(seconds=offset), device_id=device)
    assert _incoming_wins(cat, incoming) is expected


@pytest.mark.asyncio
async def test_DATA07_collect_boundary_and_replay(db):
    ident = uid("boundary-task")
    async with db.factory() as session:
        session.add(QuintalTask(id=ident, user_id=uid("A"), kind="order", target="chicken", status="active",
                                started_at=NOW-timedelta(minutes=10), ready_at=NOW, inputs={}, outputs={"chicken": 2}))
        await session.commit()
    async def collect(who, key, clock):
        async with db.factory() as session:
            await progression.account(session, uid(who))
            result = await operation(session, uid(who), "collect", str(ident), {}, key, now=clock)
            await session.commit()
            return result
    with pytest.raises(ValueError):
        await collect("A", uid("early"), NOW-timedelta(milliseconds=1))
    with pytest.raises(ValueError):
        await collect("B", uid("foreign-collect"), NOW)
    key = uid("boundary-collect")
    first = await collect("A", key, NOW)
    assert await collect("A", key, NOW+timedelta(milliseconds=1)) == first
    with pytest.raises(ValueError):
        await collect("A", uid("second-intent"), NOW+timedelta(milliseconds=1))
    async with db.factory() as session:
        assert (await session.get(QuintalResource, (uid("A"), "chicken"))).quantity == 2
        assert (await session.get(QuintalTask, ident)).status == "collected"
    evidence("DATA07-boundary", {"scenario": "DATA-07", "early": "rejected", "at_deadline": "collected",
                                "replay": "same", "new_key_duplicate": "rejected", "quantity": 2})


@pytest.mark.asyncio
async def test_DATA03_DATA04_concurrent_intent_and_cross_account(db):
    async def execute(who, data):
        async with db.factory() as session:
            await progression.account(session, uid(who))
            result = await operation(session, uid(who), "order", "chicken", data, uid("intent"), now=NOW)
            await session.commit()
            return result
    first, replay = await asyncio.gather(execute("A", {"payment": "coins"}), execute("A", {"payment": "coins"}))
    assert first == replay
    with pytest.raises(ValueError):
        await execute("A", {"payment": "products"})
    await execute("B", {"payment": "coins"})
    async with db.factory() as session:
        for who in ("A", "B"):
            assert (await session.get(HabitatWallet, uid(who))).balance == 88
            assert await session.scalar(select(func.count()).select_from(HabitatTransaction).where(
                HabitatTransaction.user_id == uid(who), HabitatTransaction.event_key == f'quintal:{uid("intent")}')) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revisions,limits", [(10, (1, 200, 500)), (100, (200, 500))])
async def test_DB02_history_pagination_and_latest_revision(db, revisions, limits):
    # Bound the 100-revision case to larger pages; limit=1 is covered by the 10-revision case.
    async with db.factory() as session:
        for index in range(40):
            cat = RemoteCat(id=uid(f"history-cat-{index}"), user_id=uid("A"), client_id=uid(f"history-client-{index}"),
                            client_updated_at=NOW, updated_by_device="a1", payload={"unknown": [index]})
            session.add(cat)
            await session.flush()
            for _ in range(revisions):
                session.add(SyncChange(user_id=uid("A"), cat_id=cat.id))
        await session.flush()
        extra = SyncChange(user_id=uid("A"), cat_id=uid("history-cat-0"))
        session.add(extra)
        await session.commit()
        latest = extra.revision
    samples = []
    for limit in limits:
        cursor, seen, page = 0, set(), 0
        while True:
            started = time.perf_counter()
            async with db.factory() as session:
                expected = list((await session.scalars(select(SyncChange).where(
                    SyncChange.user_id == uid("A"), SyncChange.revision > cursor).order_by(SyncChange.revision).limit(limit))).all())
                count = []
                def observe(conn, cur, statement, parameters, context, many):
                    if statement.lstrip().upper().startswith("SELECT"):
                        count.append(statement)
                event.listen(db.engine.sync_engine, "before_cursor_execute", observe)
                try:
                    result = await pull(cursor=cursor, limit=limit, user=db.users["A"], session=session)
                finally:
                    event.remove(db.engine.sync_engine, "before_cursor_execute", observe)
            expected_cursor = expected[-1].revision if expected else cursor
            assert result.next_cursor == expected_cursor
            mapping = {uid(f"history-cat-{i}"): uid(f"history-client-{i}") for i in range(40)}
            assert [r.client_id for r in result.changes] == [mapping[cid] for cid in dict.fromkeys(r.cat_id for r in expected)]
            assert len(count) == (4 if expected else 1)
            if page == 0:
                assert result.changes[0].revision == latest
            seen.update(str(r.client_id) for r in result.changes)
            samples.append({"limit": limit, "page": page, "cursor": cursor, "next_cursor": result.next_cursor,
                            "selects_excluding_auth": len(count), "elapsed_ms_including_oracle": (time.perf_counter()-started)*1000})
            if not expected:
                break
            assert result.next_cursor > cursor
            cursor = result.next_cursor
            page += 1
        assert cursor == latest and len(seen) == 40
    async with db.factory() as session:
        await session.execute(text("ANALYZE sync_changes"))
        query = select(SyncChange.cat_id, func.max(SyncChange.revision)).where(
            SyncChange.cat_id.in_([uid(f"history-cat-{i}") for i in range(40)])).group_by(SyncChange.cat_id)
        compiled = str(query.compile(dialect=db.engine.dialect, compile_kwargs={"literal_binds": True}))
        plan = await session.scalar(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + compiled))
    evidence("DB02-history-"+str(revisions), {"scenario": "DB-02", "cats": 40, "revisions": 40*revisions+1,
                               "samples": samples, "max_revision_query_plan": plan,
                               "timing": "includes independent oracle; functional diagnostic, not confirmatory latency"})
