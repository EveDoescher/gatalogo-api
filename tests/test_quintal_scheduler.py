from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.persistence_models import User, HabitatWallet, AppNotification
from app.quintal_models import QuintalFacility, QuintalTask, QuintalPlot, QuintalResource
from app.services import quintal_scheduler as scheduler
from app.services.fcm_service import _enabled_for_device, _copy_for


@pytest.mark.asyncio
async def test_due_without_app_request_is_durable_deduplicated_and_not_collected(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    publish = AsyncMock()
    monkeypatch.setattr(scheduler, 'publish_committed_events', publish)
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    user_id = uuid4()
    task_id = uuid4()
    async with factory() as db:
        db.add_all([
            User(id=user_id, email='scheduler@test.local', status='active'),
            HabitatWallet(user_id=user_id, balance=0, care_state={}, selected_cat_ids=[], expansion_level=0),
            QuintalFacility(user_id=user_id, kind='market', level=1, target_level=0),
            QuintalTask(id=task_id, user_id=user_id, kind='order', target='chicken',
                status='active', started_at=now-timedelta(hours=1), ready_at=now,
                inputs={}, outputs={'chicken': 1}),
            QuintalPlot(user_id=user_id, slot=0, crop='wheat',
                started_at=now-timedelta(hours=1), ready_at=now, watered=0),
        ])
        await db.commit()
    await scheduler.sweep(factory, now=now-timedelta(seconds=1))
    async with factory() as db:
        assert not (await db.scalars(select(AppNotification))).all()
    await scheduler.sweep(factory, now=now)
    await scheduler.sweep(factory, now=now+timedelta(minutes=1))
    async with factory() as db:
        notices = (await db.scalars(select(AppNotification).where(AppNotification.type == 'quintal_ready'))).all()
        assert {n.payload['space'] for n in notices} == {'market', 'garden'}
        assert len(notices) == 2
        assert (await db.get(QuintalTask, task_id)).status == 'ready'
        assert (await db.get(QuintalPlot, (user_id, 0))).crop == 'wheat'
        assert not (await db.scalars(select(QuintalResource))).all()
    assert publish.await_count == 3
    await engine.dispose()


def test_quintal_preference_is_independent_of_social_and_sightings():
    assert not _enabled_for_device({'quintal_notifications': False}, 'quintal_ready')
    assert not _enabled_for_device({'quintal_notifications': False}, 'habitat_gift')
    assert _enabled_for_device({'social_notifications': False}, 'quintal_ready')
    assert _enabled_for_device({'quintal_notifications': False}, 'possible_match')
    assert 'Quintal' in _copy_for('quintal_ready')[0]
