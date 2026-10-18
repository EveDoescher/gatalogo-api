from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.config import get_settings
from app.persistence_models import User, HabitatWallet, HabitatTransaction
from app.quintal_models import QuintalCatCare, QuintalTask, QuintalCounter
from app.services import quintal_production as production, progression, milestones
from app.services.quintal_progress import publish_progress


@pytest.mark.asyncio
async def test_offline_meals_and_cooking_keep_local_day_week_and_do_not_republish(monkeypatch):
    monkeypatch.setattr(get_settings(), 'progression_rollout_percent', 100)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        user_id, cat_id = uuid4(), uuid4()
        # Sunday 23:55 in São Paulo; the second meal is Monday 11:55.
        served = datetime(2026, 10, 5, 2, 55, tzinfo=UTC)
        returned = datetime(2026, 10, 6, 15, tzinfo=UTC)
        wallet = HabitatWallet(user_id=user_id, balance=0, expansion_level=0,
                               care_state={}, selected_cat_ids=[str(cat_id)])
        db.add_all([
            User(id=user_id, email='calendar@test.local', status='active'), wallet,
            QuintalCatCare(user_id=user_id, cat_id=cat_id,
                state={'recipe': 'basic', 'portions': 2, 'served_at': served.isoformat()}),
            QuintalTask(id=uuid4(), user_id=user_id, kind='cook', target='garden',
                status='active', started_at=served,
                ready_at=datetime(2026, 10, 5, 3, 5, tzinfo=UTC),
                inputs={}, outputs={'ration:garden': 3}),
            # Legacy history without dates is kept out of current periods.
            QuintalCounter(user_id=user_id, metric='harvest', quantity=5, projected_quantity=2),
        ])
        await db.commit()
        await production.reconcile(db, wallet, now=returned)
        await publish_progress(db, user_id)
        await db.commit()
        events = await progression.events_for(db, user_id)
        meals = [e for e in events if e.evidence.get('metric') == 'quintal_meal']
        assert sorted(e.evidence['local_day'] for e in meals) == ['2026-10-04', '2026-10-05']
        assert sorted(e.evidence['local_week'] for e in meals) == ['2026-09-28', '2026-10-05']
        cooked = next(e for e in events if e.evidence.get('metric') == 'quintal_cook')
        assert cooked.evidence['local_day'] == '2026-10-05'
        assert cooked.evidence['occurred_at'] == '2026-10-05T03:05:00+00:00'
        legacy = next(e for e in events if e.evidence.get('metric') == 'quintal_harvest')
        assert legacy.evidence['quantity'] == 3
        assert legacy.evidence['local_day'] is None
        assert legacy.evidence['local_week'] is None
        totals = milestones.counters(events)
        assert totals['care'] == 2  # care_day is not a second copy of the meal.
        assert totals['care_days'] == 2
        count = len(events)
        await production.reconcile(db, wallet, now=returned)
        pending = await publish_progress(db, user_id)
        assert not pending.get("bonuses")  # No new grant; unread receipts remain.
        from app.services.quintal_rewards import acknowledge_rewards
        await acknowledge_rewards(db, user_id, [r["id"] for r in pending.get("celebrations", [])])
        assert await publish_progress(db, user_id) == {}
        await db.commit()
        assert len(await progression.events_for(db, user_id)) == count
        journal = list((await db.scalars(select(HabitatTransaction).where(
            HabitatTransaction.category == 'quintal_activity'))).all())
        assert journal and all(r.details['projected'] for r in journal)
    await engine.dispose()
