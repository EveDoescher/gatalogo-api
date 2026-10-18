from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.persistence_models import User, DeviceToken, AppNotification, HabitatTransaction
from app.services import fcm_service
from app.services.quintal_push import deliver_pending

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def push_db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id, notice_id, receipt_id = uuid4(), uuid4(), uuid4()
    async with factory() as db:
        db.add_all([
            User(id=user_id, email='push@test.local', status='active'),
            AppNotification(id=notice_id, user_id=user_id, type='quintal_ready', payload={}),
            HabitatTransaction(id=receipt_id, user_id=user_id, event_key='qpush:'+str(notice_id),
                category='quintal_push', amount=0, details={'notification_id': str(notice_id),
                'state': 'pending', 'attempts': 0, 'delivered': [], 'next_attempt': 0,
                'expires_at': (NOW+timedelta(days=1)).timestamp()}),
        ])
        await db.commit()
    yield factory, user_id, notice_id, receipt_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_persists_successful_devices_and_respects_deadline(push_db):
    factory, _, _, receipt_id = push_db
    send = AsyncMock(side_effect=[{'delivered': ['phone1'], 'retry': True},
        {'delivered': ['phone1', 'phone2'], 'retry': False}])
    sender = SimpleNamespace(send_notification=send)
    await deliver_pending(factory, now=NOW, sender=sender)
    async with factory() as db:
        saved = (await db.get(HabitatTransaction, receipt_id)).details
        assert saved['state'] == 'pending' and saved['delivered'] == ['phone1']
    await deliver_pending(factory, now=NOW+timedelta(seconds=59), sender=sender)
    assert send.await_count == 1
    await deliver_pending(factory, now=NOW+timedelta(seconds=60), sender=sender)
    assert send.await_args.kwargs['delivered'] == ['phone1']
    await deliver_pending(factory, now=NOW+timedelta(hours=1), sender=sender)
    assert send.await_count == 2
    async with factory() as db:
        assert (await db.get(HabitatTransaction, receipt_id)).details['state'] == 'sent'


@pytest.mark.asyncio
async def test_failure_then_read_discards_retry(push_db):
    factory, _, notice_id, receipt_id = push_db
    sender = SimpleNamespace(send_notification=AsyncMock(side_effect=RuntimeError('offline')))
    await deliver_pending(factory, now=NOW, sender=sender)
    async with factory() as db:
        assert (await db.get(HabitatTransaction, receipt_id)).details['state'] == 'pending'
        (await db.get(AppNotification, notice_id)).read_at = NOW
        await db.commit()
    await deliver_pending(factory, now=NOW+timedelta(minutes=2), sender=sender)
    assert sender.send_notification.await_count == 1
    async with factory() as db:
        assert (await db.get(HabitatTransaction, receipt_id)).details['state'] == 'dismissed'


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['expired', 'collected'])
async def test_stale_notice_never_sends(push_db, reason):
    factory, _, _, receipt_id = push_db
    async with factory() as db:
        receipt = await db.get(HabitatTransaction, receipt_id)
        receipt.details = {**receipt.details, **(
            {'expires_at': NOW.timestamp()} if reason == 'expired'
            else {'markers': ['task:' + str(uuid4())]})}
        await db.commit()
    sender = SimpleNamespace(send_notification=AsyncMock())
    await deliver_pending(factory, now=NOW, sender=sender)
    sender.send_notification.assert_not_awaited()
    async with factory() as db:
        assert (await db.get(HabitatTransaction, receipt_id)).details['state'] == 'dismissed'


@pytest.mark.asyncio
async def test_real_transport_retries_only_failed_device_and_keeps_bad_request_token(push_db, monkeypatch):
    factory, user_id, notice_id, _ = push_db
    first, second = uuid4(), uuid4()
    async with factory() as db:
        db.add_all([DeviceToken(id=first,user_id=user_id,token='phone1',platform='android'),
                    DeviceToken(id=second,user_id=user_id,token='phone2',platform='android')])
        await db.commit()
    calls = []
    def handle(request):
        import json
        token = json.loads(request.content)['message']['token']
        calls.append(token)
        return httpx.Response(400 if token == 'phone2' else 200, json={'error': {'details': []}})
    client = httpx.AsyncClient
    monkeypatch.setattr(fcm_service, 'get_session_factory', lambda: factory)
    monkeypatch.setattr(fcm_service.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handle)))
    sender = fcm_service.FcmSender()
    monkeypatch.setattr(sender, '_access_token', lambda: ('test', 'local'))
    first_report = await sender.send_notification(user_id=user_id, notification_id=str(notice_id), notification_type='quintal_ready')
    assert first_report == {'delivered': [str(first)], 'retry': True}
    calls.clear()
    await sender.send_notification(user_id=user_id, notification_id=str(notice_id), notification_type='quintal_ready', delivered=first_report['delivered'])
    assert calls == ['phone2']
    async with factory() as db:
        assert len((await db.scalars(select(DeviceToken))).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['coop', 'pasture', 'orchard'])
async def test_old_resident_notice_does_not_resend_for_a_new_ready_cycle(push_db, kind):
    from app.quintal_models import QuintalResident
    factory, user_id, notice_id, receipt_id = push_db
    old_cycle = (NOW - timedelta(hours=8)).isoformat()
    new_cycle = NOW.isoformat()
    new_notice_id, new_receipt_id = uuid4(), uuid4()
    async with factory() as db:
        old = await db.get(HabitatTransaction, receipt_id)
        old.details = {**old.details, 'markers': [f'resident:{kind}:{old_cycle}']}
        db.add(QuintalResident(user_id=user_id, kind=kind,
            state={'count': 2, 'ready': 2, 'ready_since': new_cycle}))
        db.add(AppNotification(id=new_notice_id, user_id=user_id, type='quintal_ready', payload={'space': kind}))
        db.add(HabitatTransaction(id=new_receipt_id, user_id=user_id,
            event_key='qpush:'+str(new_notice_id), category='quintal_push', amount=0,
            details={**old.details, 'notification_id': str(new_notice_id),
                     'markers': [f'resident:{kind}:{new_cycle}']}))
        await db.commit()
    sender = SimpleNamespace(send_notification=AsyncMock(return_value={'delivered': ['phone'], 'retry': False}))
    await deliver_pending(factory, now=NOW, sender=sender)
    sender.send_notification.assert_awaited_once()
    assert sender.send_notification.await_args.kwargs['notification_id'] == str(new_notice_id)
    async with factory() as db:
        assert (await db.get(HabitatTransaction, receipt_id)).details['state'] == 'dismissed'
        assert (await db.get(HabitatTransaction, new_receipt_id)).details['state'] == 'sent'
        assert (await db.get(QuintalResident, (user_id, kind))).state['ready'] == 2
        assert (await db.get(AppNotification, notice_id)).read_at is None
