from datetime import UTC, datetime, timedelta
from uuid import uuid4, UUID
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.persistence_models import User, HabitatWallet, HabitatItem, HabitatTransaction, AppNotification
from app.services.habitat_encounters import visit, deliver, claim, satisfaction


class Lucky:
    def random(self): return 0
    def choice(self, rewards): return rewards[0]


@pytest_asyncio.fixture
async def habitat_account():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for model in (User, HabitatWallet, HabitatItem, HabitatTransaction, AppNotification):
            await connection.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(id=uuid4(), email='visitor@test.local', status='active')
        wallet = HabitatWallet(user_id=user.id, care_state={}, selected_cat_ids=[], cat_anchors={})
        session.add_all([user, wallet]); await session.commit()
        yield session, user, wallet
    await engine.dispose()


@pytest.mark.asyncio
async def test_gift_visits_notify_once_and_claim_once(habitat_account):
    session, user, wallet = habitat_account
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    await visit(session, user.id, now=now, rng=Lucky()); await session.commit()
    gift = wallet.care_state['pending_gift']
    gift_id = UUID(gift['id'])
    assert gift['state'] == 'visiting' and wallet.balance == 0
    await visit(session, user.id, now=now+timedelta(seconds=1), rng=Lucky())
    assert wallet.care_state['pending_gift']['id'] == str(gift_id)
    with pytest.raises(ValueError): await claim(session, user.id, gift_id)
    await deliver(session, user.id, gift_id, now=now+timedelta(seconds=25))
    await session.commit()
    await deliver(session, user.id, gift_id, now=now+timedelta(seconds=26))
    await session.commit()
    notices = list((await session.scalars(select(AppNotification))).all())
    assert len(notices) == 1 and notices[0].payload['gift_id'] == str(gift_id)
    notices[0].read_at = now; await session.commit()
    assert wallet.balance == 0 and wallet.care_state['pending_gift']['state'] == 'available'
    reward = await claim(session, user.id, gift_id); await session.commit()
    assert await claim(session, user.id, gift_id) == reward
    assert wallet.balance == 12 and 'pending_gift' not in wallet.care_state
    await visit(session, user.id, now=now+timedelta(seconds=40), rng=Lucky())
    assert 'pending_gift' not in wallet.care_state
    with pytest.raises(ValueError): await claim(session, user.id, uuid4())


@pytest.mark.asyncio
async def test_recovery_limits_and_satisfaction(habitat_account):
    session, user, wallet = habitat_account
    now = datetime(2026,10,4,12,tzinfo=UTC)
    for index in range(3):
        current = now+timedelta(minutes=21*index)
        await visit(session,user.id,now=current,rng=Lucky()); await session.commit()
        gift_id = UUID(wallet.care_state['pending_gift']['id'])
        await deliver(session,user.id,now=current+timedelta(minutes=2),recover=True); await session.commit()
        await claim(session,user.id,gift_id); await session.commit()
    await visit(session,user.id,now=now+timedelta(hours=2),rng=Lucky())
    assert 'pending_gift' not in wallet.care_state and wallet.balance == 36
    assert satisfaction(wallet,[],now)['value'] == 0
    wallet.selected_cat_ids = ['cat']
    wallet.care_state = {**wallet.care_state,'cats':{'cat':{'feed_day':now.date().isoformat(),'pet_day':now.date().isoformat()}}}
    assert satisfaction(wallet,[],now)['value'] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize('reward', [{'portions': 2}, {'sku': 'purple_flowers'}])
async def test_non_coin_gifts_are_persisted_and_not_duplicated(habitat_account, reward):
    session, user, wallet = habitat_account
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    class RewardRng(Lucky):
        def choice(self, rewards): return reward
    await visit(session, user.id, now=now, rng=RewardRng())
    await session.commit()
    gift_id = UUID(wallet.care_state['pending_gift']['id'])
    await deliver(session, user.id, gift_id, now=now+timedelta(seconds=25))
    await session.commit()
    assert await claim(session, user.id, gift_id) == reward
    await session.commit()
    assert await claim(session, user.id, gift_id) == reward
    if 'portions' in reward:
        assert wallet.care_state['bonus_portions'] == 2
    else:
        items = list((await session.scalars(select(HabitatItem))).all())
        assert len(items) == 1 and items[0].sku == reward['sku']
    assert wallet.balance == 0


@pytest.mark.asyncio
async def test_missed_window_cannot_be_rerolled_and_full_inventory_converts(habitat_account):
    session, user, wallet = habitat_account
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    class Unlucky(Lucky):
        def random(self): return .99
    await visit(session, user.id, now=now, rng=Unlucky())
    await session.commit()
    await visit(session, user.id, now=now+timedelta(seconds=1), rng=Lucky())
    assert 'pending_gift' not in wallet.care_state
    for index in range(4):
        session.add(HabitatItem(id=uuid4(), user_id=user.id, sku='purple_flowers', purchase_key=f'owned:{index}'))
    await session.commit()
    class Flowers(Lucky):
        def choice(self, rewards): return {'sku': 'purple_flowers'}
    await visit(session, user.id, now=now+timedelta(minutes=21), rng=Flowers())
    await session.commit()
    gift_id = UUID(wallet.care_state['pending_gift']['id'])
    with pytest.raises(ValueError):
        await deliver(session, user.id, gift_id, now=now+timedelta(minutes=21, seconds=1))
    other = User(id=uuid4(), email='other@test.local', status='active')
    session.add_all([other, HabitatWallet(user_id=other.id, care_state={})])
    await session.commit()
    with pytest.raises(ValueError):
        await claim(session, other.id, gift_id)
    await deliver(session, user.id, gift_id, now=now+timedelta(minutes=22))
    await session.commit()
    assert await claim(session, user.id, gift_id) == {'coins': 12}
    await session.commit()
    assert wallet.balance == 12
    assert len(list((await session.scalars(select(HabitatItem))).all())) == 4


@pytest.mark.asyncio
async def test_accessible_comfort_and_real_care_raise_satisfaction(habitat_account):
    _, user, wallet = habitat_account
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    wallet.selected_cat_ids = ['cat']
    wallet.cat_anchors = {'cat': [1, 1]}
    wallet.care_state = {'cats': {'cat': {'pet_day': now.date().isoformat(), 'feed_day': now.date().isoformat()}}}
    bed = HabitatItem(id=uuid4(), user_id=user.id, sku='cat_bed', x=3, y=1, rotation=0)
    toy = HabitatItem(id=uuid4(), user_id=user.id, sku='play_crate', x=1, y=3, rotation=0)
    assert satisfaction(wallet, [bed, toy], now) == {'value': 100, 'comfort': 40, 'care': 40}
    bed.x = bed.y = toy.x = toy.y = None
    assert satisfaction(wallet, [bed, toy], now)['value'] == 60
    assert satisfaction(wallet, [bed, toy], now+timedelta(days=1))['value'] == 20
