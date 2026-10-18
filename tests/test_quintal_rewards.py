from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.config import get_settings
from app.persistence_models import User, HabitatWallet, HabitatItem, HabitatTransaction
from app.quintal_models import QuintalCatCare
from app.services import quintal_production as production, progression
from app.services.quintal_progress import publish_progress
from app.services.quintal_rewards import pending_rewards, acknowledge_rewards
from app.services.quintal_catalog import BOND_REWARDS, catalog


@pytest.mark.asyncio
async def test_rewards_survive_lost_response_and_ack_is_scoped_without_new_credit(monkeypatch):
    monkeypatch.setattr(get_settings(), 'progression_rollout_percent', 100)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner, other = uuid4(), uuid4()
    async with factory() as db:
        db.add_all([User(id=owner, email='reward@test.local', status='active'),
                    User(id=other, email='other-reward@test.local', status='active')])
        await db.flush()
        await progression.account(db, owner)
        await production.counter(db, owner, 'harvest', 50, occurred_at=datetime.now(UTC))
        result = await publish_progress(db, owner)
        receipts = result['celebrations']
        awards = [b for r in receipts for b in r['bonuses']]
        assert {'start_harvest', 'garden_50'} <= {b['id'] for b in awards}
        assert next(b for b in awards if b['id'] == 'garden_50')['xp'] == 400
        before = await progression.snapshot(db, owner)
        item_ids = set(await db.scalars(select(HabitatItem.id).where(HabitatItem.user_id == owner)))
        await db.commit()  # Simulate a committed response lost before reaching the app.
    async with factory() as db:
        repeated = await publish_progress(db, owner)
        assert repeated == {'celebrations': receipts}
        ids = [r['id'] for r in receipts]
        assert await acknowledge_rewards(db, other, ids) == 0
        assert await acknowledge_rewards(db, owner, []) == 0
        assert await acknowledge_rewards(db, owner, ['unknown']) == 0
        assert await pending_rewards(db, owner) == receipts
        key = uuid4()
        for _ in range(2):
            answer = await production.operation(db, owner, 'ack_rewards', 'quintal', {'event_ids': ids}, key)
            assert answer['acknowledged'] == len(ids)
        assert await publish_progress(db, owner) == {}
        after = await progression.snapshot(db, owner)
        assert (after['xp'], after['balance']) == (before['xp'], before['balance'])
        assert set(await db.scalars(select(HabitatItem.id).where(HabitatItem.user_id == owner))) == item_ids
        await db.commit()
    async with factory() as db:
        assert await pending_rewards(db, owner) == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_all_bond_rewards_match_catalog_and_reconcile_never_duplicates():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        owner, cat = uuid4(), uuid4()
        wallet = HabitatWallet(user_id=owner, balance=0, expansion_level=0,
                               care_state={}, selected_cat_ids=[])
        db.add_all([User(id=owner, email='bond-reward@test.local', status='active'), wallet,
                    QuintalCatCare(user_id=owner, cat_id=cat, state={'affinity': 200})])
        await db.flush()
        await production.reconcile(db, wallet)
        first = await pending_rewards(db, owner)
        assert len(first) == 4
        assert {(r['threshold'], r['items'][0]['sku']) for r in first} == set(BOND_REWARDS)
        assert all(r['cat_id'] == str(cat) for r in first)
        assert {r['items'][0]['name'] for r in first} == {r['name'] for r in catalog()['bond_rewards']}
        assert all(r['destination'] == 'decorations' for r in catalog()['bond_rewards'])
        await production.reconcile(db, wallet)
        assert await pending_rewards(db, owner) == first
        items = list(await db.scalars(select(HabitatItem).where(HabitatItem.user_id == owner)))
        assert len(items) == 4
        await acknowledge_rewards(db, owner, [first[0]['id']])
        remaining = await pending_rewards(db, owner)
        assert len(remaining) == 3
        assert first[0]['id'] not in {r['id'] for r in remaining}
    await engine.dispose()
