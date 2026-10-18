"""Opt-in PostgreSQL transaction checks in an isolated, disposable schema.

Run with QUINTAL_POSTGRES_TEST=1. Uses configured DB credentials without logging
them; never writes to public application tables or an existing player account.
"""
import asyncio
import os
from uuid import uuid4
from datetime import UTC, datetime, timedelta
import pytest
import pytest_asyncio
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import get_settings
from app.database import Base
from app.persistence_models import User, HabitatWallet
from app.quintal_models import QuintalFacility, QuintalTask, QuintalResource
from app.services import progression
from app.services.quintal_production import operation

pytestmark = pytest.mark.skipif(os.getenv('QUINTAL_POSTGRES_TEST') != '1', reason='Explicit PostgreSQL opt-in required')


@pytest_asyncio.fixture
async def postgres(monkeypatch):
    url = get_settings().database_url
    if not url.startswith('postgresql'):
        pytest.fail('Configured database must be PostgreSQL')
    schema = 'quintal_test_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema + ',public'}})
    try:
        async with engine.begin() as conn:
            assert await conn.scalar(text('select current_schema()')) == schema
            # Force creation in the new schema; public is only needed for pgvector.
            # checkfirst=True could incorrectly reuse an existing public table.
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id, other_id = uuid4(), uuid4()
        async with factory() as db:
            db.add_all([User(id=uid, email=f'{uid}@test.local', status='active')
                        for uid in (user_id, other_id)])
            await db.flush()  # PostgreSQL enforces FKs before dependent inserts.
            for uid in (user_id, other_id):
                db.add_all([HabitatWallet(user_id=uid, balance=100, care_state={}, selected_cat_ids=[], expansion_level=0),
                    QuintalFacility(user_id=uid, kind='market', level=1, target_level=0)])
            await db.commit()
        monkeypatch.setattr(get_settings(), 'habitat_free_shop', False)
        yield factory, engine, user_id, other_id
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            assert schema.startswith('quintal_test_') and len(schema) == 45
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def transact(factory, user_id, action, target, key, data=None):
    async with factory() as db:
        await progression.account(db, user_id)
        result = await operation(db, user_id, action, target, data or {}, key)
        await db.commit()
        return result


@pytest.mark.asyncio
async def test_two_clients_and_lost_response_replay_after_reconnect(postgres):
    factory, engine, user_id, _ = postgres
    key = uuid4()
    results = await asyncio.gather(*[
        transact(factory, user_id, 'order', 'chicken', key, {'payment': 'coins'})
        for _ in range(2)])
    assert results[0] == results[1]
    await engine.dispose()  # Commit survives reconnection even if its response was lost.
    assert await transact(factory, user_id, 'order', 'chicken', key, {'payment': 'coins'}) == results[0]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(QuintalTask)) == 1
        assert (await db.get(HabitatWallet, user_id)).balance == 88


@pytest.mark.asyncio
async def test_concurrent_collection_and_cross_account_access(postgres):
    factory, _, user_id, other_id = postgres
    task_id = uuid4()
    now = datetime.now(UTC)
    async with factory() as db:
        db.add(QuintalTask(id=task_id, user_id=user_id, kind='order', target='chicken',
            status='ready', started_at=now-timedelta(hours=2), ready_at=now-timedelta(hours=1),
            inputs={}, outputs={'chicken': 2}))
        await db.commit()
    with pytest.raises(ValueError):
        await transact(factory, other_id, 'collect', str(task_id), uuid4())
    results = await asyncio.gather(*[
        transact(factory, user_id, 'collect', str(task_id), uuid4()) for _ in range(2)],
        return_exceptions=True)
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert sum(isinstance(r, dict) for r in results) == 1
    async with factory() as db:
        assert (await db.get(QuintalResource, (user_id, 'chicken'))).quantity == 2
        assert await db.get(QuintalResource, (other_id, 'chicken')) is None


@pytest.mark.asyncio
async def test_push_workers_share_one_receipt_and_retry_after_reconnect(postgres):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.persistence_models import AppNotification, HabitatTransaction
    from app.services.quintal_push import deliver_pending
    factory, engine, user_id, _ = postgres
    now = datetime.now(UTC)
    notice_id, receipt_id = uuid4(), uuid4()
    async with factory() as db:
        db.add(AppNotification(id=notice_id, user_id=user_id, type="quintal_ready", payload={}))
        await db.flush()
        db.add(HabitatTransaction(id=receipt_id, user_id=user_id, event_key=f"qpush:{notice_id}",
            category="quintal_push", amount=0, details={"notification_id": str(notice_id),
            "state": "pending", "attempts": 0, "delivered": [], "next_attempt": 0,
            "expires_at": (now + timedelta(days=1)).timestamp()}))
        await db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    async def first_delivery(**kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return {"delivered": ["device-a"], "retry": True}
    sender = SimpleNamespace(send_notification=AsyncMock(side_effect=first_delivery))
    first = asyncio.create_task(deliver_pending(factory, now=now, sender=sender))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await deliver_pending(factory, now=now, sender=sender)  # SKIP LOCKED on actual PG
    finally:
        release.set()
        await first
    sender.send_notification.assert_awaited_once()
    await engine.dispose()
    sender.send_notification = AsyncMock(return_value={"delivered": ["device-a", "device-b"], "retry": False})
    await deliver_pending(factory, now=now + timedelta(seconds=60), sender=sender)
    sender.send_notification.assert_awaited_once()
    assert sender.send_notification.await_args.kwargs["delivered"] == ["device-a"]
    async with factory() as db:
        receipt = await db.get(HabitatTransaction, receipt_id)
        assert receipt.details["state"] == "sent" and receipt.details["attempts"] == 2
        assert receipt.details["delivered"] == ["device-a", "device-b"]


@pytest.mark.asyncio
async def test_activity_calendar_survives_projection_and_reconnection(postgres):
    from app.persistence_models import ProgressEvent
    from app.quintal_models import QuintalCounter
    from app.services.quintal_production import counter
    from app.services.quintal_progress import publish_progress
    factory, engine, user_id, _ = postgres
    # Both events are replayed on a later day, but keep their original local periods.
    sunday = datetime(2026, 10, 12, 2, 59, tzinfo=UTC)
    monday = datetime(2026, 10, 12, 3, 1, tzinfo=UTC)
    async with factory() as db:
        for when in (sunday, monday):
            await counter(db, user_id, "harvest", 1, occurred_at=when)
        await db.commit()
    async with factory() as db:
        await progression.account(db, user_id)
        await publish_progress(db, user_id)
        await db.commit()
    await engine.dispose()
    async with factory() as db:
        await progression.account(db, user_id)
        await publish_progress(db, user_id)
        await db.commit()
        events = list((await db.scalars(select(ProgressEvent).where(
            ProgressEvent.user_id == user_id, ProgressEvent.kind == "quintal"))).all())
        assert len(events) == 2
        assert {(e.evidence["local_day"], e.evidence["local_week"]) for e in events} == {
            ("2026-10-11", "2026-10-05"), ("2026-10-12", "2026-10-12")}
        row = await db.get(QuintalCounter, (user_id, "harvest"))
        assert row.quantity == row.projected_quantity == 2
