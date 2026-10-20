from datetime import UTC, datetime, timedelta
from uuid import uuid4
import math
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.persistence_models import User, HabitatWallet, HabitatItem, HabitatTransaction, RemoteCat
from app.quintal_models import (QuintalFacility, QuintalResource, QuintalTask, QuintalPlot,
    QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference, QuintalSession)
from app.services import quintal_production as p
from app.services.quintal_catalog import catalog
from app.services.quintal_games import validate_trace, fish_position

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)

@pytest.fixture(autouse=True)
def regular_build_times(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "habitat_instant_build", False)


@pytest_asyncio.fixture
async def account():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (User, HabitatWallet, HabitatItem, HabitatTransaction, RemoteCat, QuintalFacility, QuintalResource, QuintalTask, QuintalPlot, QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference, QuintalSession):
            await connection.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(id=uuid4(), email="production@test.local", status="active")
        wallet = HabitatWallet(user_id=user.id, balance=1000, expansion_level=3, care_state={"cats": {"legacy": {"pet_day": "2026-10-06"}}}, selected_cat_ids=[])
        session.add_all([user, wallet])
        await session.commit()
        yield session, user, wallet
    await engine.dispose()

async def op(session, user, action, target, data=None, now=NOW, key=None):
    result = await p.operation(session, user.id, action, target, data or {}, key or uuid4(), now=now)
    await session.commit()
    return result


@pytest.mark.asyncio
async def test_return_receipt_only_acknowledges_presented_events(account):
    session, user, wallet = account
    first = QuintalTask(id=uuid4(), user_id=user.id, kind='order', target='chicken',
        status='ready', started_at=NOW-timedelta(hours=1), ready_at=NOW,
        inputs={}, outputs={'chicken': 2})
    session.add(first)
    await session.commit()
    shown = (await p.projection(session, user.id, now=NOW))['updates']
    await session.commit()
    second = QuintalTask(id=uuid4(), user_id=user.id, kind='order', target='beef',
        status='ready', started_at=NOW-timedelta(hours=1), ready_at=NOW,
        inputs={}, outputs={'beef': 2})
    session.add(second)
    await session.commit()
    await op(session, user, 'ack_return', 'quintal', {'event_ids': [shown[0]['event_id']]})
    remaining = (await p.projection(session, user.id, now=NOW))['updates']
    assert [r['id'] for r in remaining] == [str(second.id)]
    # An old client omitting receipts must not swallow unseen updates.
    await op(session, user, 'ack_return', 'quintal')
    assert (await p.projection(session, user.id, now=NOW))['updates'] == remaining
    assert first.status == second.status == 'ready'


@pytest.mark.asyncio
async def test_return_preserves_two_meals_and_individual_ack(account):
    session, user, wallet = account
    care = QuintalCatCare(user_id=user.id, cat_id=uuid4(), state={
        'portions': 2, 'recipe': 'basic', 'served_at': p.iso(NOW-timedelta(hours=12)),
    })
    session.add(care)
    await session.flush()
    await p.meals(session, wallet, care, NOW, p.random.Random(1))
    await session.commit()
    shown = (await p.projection(session, user.id, now=NOW))['updates']
    assert len(shown) == 2 and all(r['kind'] == 'meal' for r in shown)
    assert shown[0]['event_id'] != shown[1]['event_id']
    await op(session, user, 'ack_return', 'quintal', {'event_ids': [shown[0]['event_id']]})
    remaining = (await p.projection(session, user.id, now=NOW))['updates']
    assert [r['event_id'] for r in remaining] == [shown[1]['event_id']]


@pytest.mark.asyncio
async def test_play_result_reports_actual_bond_and_replays_without_credit(account):
    from app.services.quintal_games import session_operation
    session, user, wallet = account
    cat = uuid4()
    session.add(RemoteCat(user_id=user.id, client_id=cat, client_updated_at=NOW,
                          updated_by_device='test', payload={'name': 'Mimi', 'is_owned': True}))
    wallet.selected_cat_ids = [str(cat)]
    session.add(QuintalCatCare(user_id=user.id, cat_id=cat,
                              state={'affinity': 12, 'assisted': False}))
    await session.commit()
    trace = [{'t': float(i), 'position': [.2, .5, .8][i % 3]} for i in range(21)]
    for offset, expected_gain in [(0, 2), (60, 0)]:
        started = await session_operation(session, user.id, 'start_play', str(cat), {}, uuid4(), now=NOW + timedelta(seconds=offset))
        finished = NOW + timedelta(seconds=offset + 20)
        key = uuid4()
        result = await session_operation(session, user.id, 'finish_play', started['session_id'], {'trace': trace}, key, now=finished)
        await session.commit()
        effect = result['care_effect']
        assert effect['bond_gained'] == expected_gain
        assert effect['bond_total'] == 14
        assert effect['play_bonus_after'] == 20
        assert p.utc(effect['play_bonus_expires_at']) == finished + timedelta(hours=24)
        replay = await session_operation(session, user.id, 'finish_play', started['session_id'], {'trace': trace}, key, now=finished + timedelta(seconds=1))
        assert replay == result
        care = await session.get(QuintalCatCare, (user.id, cat))
        assert care.state['affinity'] == 14
    assert p.play_care_bonus({'played_at': p.iso(NOW), 'assisted': False}, NOW + timedelta(hours=12)) == 10
    assert p.play_care_bonus({'played_at': p.iso(NOW), 'assisted': False}, NOW + timedelta(hours=24)) == 0


@pytest.mark.asyncio
async def test_build_interval_survives_reload_replay_and_finishes_explicitly(account):
    session, user, wallet = account
    key = uuid4()
    await op(session, user, 'build', 'garden:1', key=key)
    session.expire_all()
    await session.refresh(user)
    saved = await session.get(QuintalFacility, (user.id, 'garden'))
    assert p.utc(saved.started_at) == NOW
    end = p.utc(saved.ready_at)
    await op(session, user, 'build', 'garden:1', key=key, now=NOW + timedelta(seconds=1))
    assert p.utc(saved.started_at) == NOW
    assert p.utc(saved.ready_at) == end
    projection = await p.projection(session, user.id, end)
    building = next(f for f in projection['facilities'] if f['kind'] == 'garden')
    assert p.utc(building['started_at']) == NOW
    assert building['level'] == 0
    assert building['target_level'] == 1
    await op(session, user, 'finish_build', 'garden', now=end)
    assert saved.started_at is None
    assert saved.ready_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,level', [('garden', 1), ('garden', 2), ('coop', 1), ('pasture', 1), ('kitchen', 1)])
async def test_expansion_preserves_existing_production_and_unlocks_only_on_finish(account, kind, level):
    session, user, wallet = account
    end = NOW + timedelta(hours=2)
    session.add(QuintalFacility(user_id=user.id, kind=kind, level=level,
                               target_level=level + 1, ready_at=end))
    if kind == 'garden':
        for slot in range(level * 4):
            session.add(QuintalPlot(user_id=user.id, slot=slot, crop='corn',
                                   started_at=NOW, ready_at=NOW + timedelta(minutes=30), watered=0))
    elif kind in ('coop', 'pasture'):
        count = 2 if kind == 'coop' else 1
        session.add(QuintalResident(user_id=user.id, kind=kind, state={
            'count': count, 'cursor': p.iso(NOW),
            'animals': [{'slot': i, 'ready': 1, 'cursor': p.iso(NOW), 'progress': 0}
                        for i in range(count)],
        }))
    await session.commit()
    if kind == 'kitchen':
        await p.stock(session, user.id, {'wheat': 4, 'carrot': 2})
        cooked = await op(session, user, 'cook', 'garden')
    before = await p.projection(session, user.id, end)
    assert next(f for f in before['facilities'] if f['kind'] == kind)['level'] == level
    key = uuid4()
    await op(session, user, 'finish_build', kind, now=end, key=key)
    await op(session, user, 'finish_build', kind, now=end, key=key)
    after = await p.projection(session, user.id, end)
    facility = next(f for f in after['facilities'] if f['kind'] == kind)
    assert facility['level'] == level + 1 and facility['target_level'] == 0
    if kind == 'garden':
        assert len(before['plots']) == level * 4
        assert len(after['plots']) == (level + 1) * 4
        assert after['plots'][:level * 4] == before['plots']
        assert all(plot['crop'] is None for plot in after['plots'][level * 4:])
    elif kind in ('coop', 'pasture'):
        old = next(r for r in before['residents'] if r['kind'] == kind)
        new = next(r for r in after['residents'] if r['kind'] == kind)
        assert old['count'] == count and new['count'] == count * 2
        assert [a['ready'] for a in new['animals'][:count]] == [1] * count
        assert all(a['ready'] == 0 for a in new['animals'][count:])
    elif kind == 'kitchen':
        assert before['tasks'] == after['tasks']
        assert len(after['tasks']) == 1 and after['tasks'][0]['status'] == 'ready'
        await op(session, user, 'collect', cooked['task_id'], now=end)
        collected = await p.projection(session, user.id, end)
        assert collected['stock']['ration:garden'] == 3

@pytest.mark.asyncio
async def test_resident_projection_explains_pause_and_next_collection(account):
    session, user, wallet = account
    session.add(QuintalResident(user_id=user.id, kind='coop', state={'count':2,'cursor':p.iso(NOW)}))
    await p.stock(session,user.id,{'corn':4})
    state = (await p.projection(session,user.id,NOW))['residents'][0]
    assert state['production_status'] == 'needs_food'
    assert state['production_progress']['pause_reason'] == 'needs_food'
    assert state['next_ready_at'] is None
    await op(session,user,'feed_residents','coop')
    state = (await p.projection(session,user.id,NOW))['residents'][0]
    assert state['production_status'] == 'needs_water'
    assert state['production_progress']['pause_reason'] == 'needs_water'
    await op(session,user,'water_residents','coop')
    state = (await p.projection(session,user.id,NOW+timedelta(hours=2)))['residents'][0]
    assert state['production_status'] == 'producing'
    assert state['production_progress']['pause_reason'] is None
    assert state['production_progress']['elapsed_seconds'] == 7200
    assert state['production_progress']['cycle_seconds'] == 21600
    assert p.utc(state['next_ready_at']) == NOW+timedelta(hours=6)
    state = (await p.projection(session,user.id,NOW+timedelta(hours=13)))['residents'][0]
    assert state['production_status'] == 'full'
    assert state['ready'] == state['capacity'] == 4
    assert state['next_ready_at'] is None

@pytest.mark.asyncio
async def test_full_production_unlock_cycle_uses_time_and_real_resources(account,monkeypatch):
    session,user,wallet=account
    monkeypatch.setattr(p,'effective_price',lambda base:0)
    await op(session,user,'build','garden:1')
    await op(session,user,'finish_build','garden')
    for slot,crop in enumerate(['corn','wheat','carrot']):
        await op(session,user,'plant',str(slot),{'crop':crop})
    harvest_time=NOW+timedelta(hours=1)
    for slot in range(3): await op(session,user,'harvest',str(slot),now=harvest_time)
    await op(session,user,'build','kitchen:1',now=harvest_time)
    kitchen_time=harvest_time+timedelta(minutes=10)
    await op(session,user,'finish_build','kitchen',now=kitchen_time)
    lot=await op(session,user,'cook','garden',now=kitchen_time)
    ready_time=kitchen_time+timedelta(minutes=10)
    await op(session,user,'collect',lot['task_id'],now=ready_time)
    for facility in ['pier','coop','market']:
        await op(session,user,'build',facility+':1',now=ready_time)
    after_builds=ready_time+timedelta(hours=1)
    for facility in ['pier','coop','market']:
        await op(session,user,'finish_build',facility,now=after_builds)
    await op(session,user,'feed_residents','coop',now=after_builds)
    await op(session,user,'water_residents','coop',now=after_builds)
    order=await op(session,user,'order','chicken',{'payment':'coins'},now=after_builds)
    after_day=after_builds+timedelta(hours=13)
    await op(session,user,'collect',order['task_id'],now=after_day)
    await op(session,user,'collect_residents','coop',now=after_day)
    snapshot=await p.projection(session,user.id,after_day)
    assert snapshot['stock']['ration:garden']==3
    assert snapshot['stock']['egg']==4
    assert snapshot['stock']['chicken']==2
    assert snapshot['stock']['corn']==0
    assert next(r for r in snapshot['residents'] if r['kind']=='coop')['count']==2
    assert next(f for f in snapshot['facilities'] if f['kind']=='rod')['level']==1
    assert wallet.balance==1000

@pytest.mark.asyncio
async def test_crop_repeat_collection_water_and_free_prices(account, monkeypatch):
    session, user, wallet = account
    monkeypatch.setattr(p, "effective_price", lambda base: 0)
    await op(session, user, "build", "garden:1")
    await op(session, user, "finish_build", "garden")
    key = uuid4()
    await op(session, user, "plant", "0", {"crop": "corn"}, key=key)
    await op(session, user, "plant", "0", {"crop": "corn"}, key=key)
    with pytest.raises(ValueError): await op(session, user, "plant", "1", {"crop": "corn"}, key=key)
    await session.rollback(); await session.refresh(user)
    await op(session, user, "water_crop", "0")
    with pytest.raises(ValueError): await op(session, user, "water_crop", "0")
    await session.rollback(); await session.refresh(user)
    await op(session, user, "harvest", "0", now=NOW + timedelta(minutes=30))
    with pytest.raises(ValueError): await op(session, user, "harvest", "0", now=NOW + timedelta(minutes=30))
    await session.rollback(); await session.refresh(user)
    await op(session, user, "build", "kitchen:1", now=NOW + timedelta(minutes=30))
    projection = await p.projection(session, user.id, NOW + timedelta(minutes=30))
    assert projection["stock"]["corn"] == 2
    assert projection["counters"]["harvest"] == 1
    await session.refresh(wallet)
    assert wallet.balance == 1000
    assert next(f for f in catalog()["facilities"] if f["id"] == "kitchen:1")["base_price"] == 40
    with pytest.raises(ValueError): await op(session, user, "finish_build", "kitchen", now=NOW + timedelta(minutes=31))

@pytest.mark.asyncio
async def test_queue_reserves_refunds_and_finishes_offline(account):
    session, user, wallet = account
    session.add(QuintalFacility(user_id=user.id, kind="kitchen", level=2, target_level=0))
    await p.stock(session, user.id, {"wheat": 6, "carrot": 3})
    a = await op(session, user, "cook", "garden")
    b = await op(session, user, "cook", "garden")
    c = await op(session, user, "cook", "garden")
    queued = await p.projection(session, user.id, NOW)
    by_id = {t["id"]: t for t in queued["tasks"]}
    assert queued["stock"]["wheat"] == queued["stock"]["carrot"] == 0
    assert p.utc(by_id[a["task_id"]]["ready_at"]) == p.utc(by_id[b["task_id"]]["started_at"])
    assert p.utc(by_id[b["task_id"]]["ready_at"]) == p.utc(by_id[c["task_id"]]["started_at"])
    cancel_key = uuid4()
    await op(session, user, "cancel", b["task_id"], key=cancel_key)
    await op(session, user, "cancel", b["task_id"], key=cancel_key)
    cancelled = await p.projection(session, user.id, NOW)
    remaining = {t["id"]: t for t in cancelled["tasks"]}
    assert b["task_id"] not in remaining
    assert cancelled["stock"]["wheat"] == 2
    assert cancelled["stock"]["carrot"] == 1
    assert p.utc(remaining[c["task_id"]]["started_at"]) == p.utc(remaining[a["task_id"]]["ready_at"])
    assert p.utc(remaining[c["task_id"]]["ready_at"]) == NOW + timedelta(minutes=20)
    assert sum(p.utc(t["started_at"]) <= NOW < p.utc(t["ready_at"]) for t in remaining.values()) == 1
    assert cancelled["stock"].get("ration:garden", 0) == 0
    with pytest.raises(ValueError): await op(session, user, "cancel", a["task_id"])
    await session.rollback(); await session.refresh(user)
    result = await p.projection(session, user.id, NOW + timedelta(minutes=21))
    assert len([t for t in result["tasks"] if t["status"] == "ready"]) == 2
    assert result["stock"]["wheat"] == 2
    key = uuid4()
    await op(session, user, "collect", a["task_id"], now=NOW + timedelta(minutes=21), key=key)
    await op(session, user, "collect", a["task_id"], now=NOW + timedelta(minutes=21), key=key)
    await op(session, user, "collect", c["task_id"], now=NOW + timedelta(minutes=21))
    result = await p.projection(session, user.id, NOW + timedelta(hours=2))
    assert result["stock"]["ration:garden"] == 6
    assert result["counters"]["cook"] == 2

@pytest.mark.asyncio
async def test_residents_pause_without_water_and_capacity_stops_time(account):
    session, user, wallet = account
    session.add(QuintalResident(user_id=user.id, kind="coop", state={"count": 2, "cursor": p.iso(NOW)}))
    await p.stock(session, user.id, {"corn": 8})
    await op(session, user, "feed_residents", "coop")
    result = await p.projection(session, user.id, NOW + timedelta(hours=10))
    assert result["residents"][0].get("ready", 0) == 0
    await op(session, user, "water_residents", "coop", now=NOW + timedelta(hours=10))
    result = await p.projection(session, user.id, NOW + timedelta(hours=23))
    assert result["residents"][0]["ready"] == 4
    await session.commit()
    await op(session, user, "collect_residents", "coop", now=NOW + timedelta(days=4))
    result = await p.projection(session, user.id, NOW + timedelta(days=4, hours=1))
    assert result["stock"]["egg"] == 4 and result["residents"][0]["ready"] == 0

@pytest.mark.asyncio
async def test_refusal_loses_portions_but_preserves_hunger_and_bond(account):
    session, user, wallet = account
    cat_id = uuid4()
    wallet.selected_cat_ids = [str(cat_id)]
    care = QuintalCatCare(user_id=user.id, cat_id=cat_id, state={"affinity": 21, "portions": 2, "recipe": "garden", "served_at": p.iso(NOW)})
    session.add_all([care, QuintalPreference(user_id=user.id, cat_id=cat_id, recipe="garden", state={"level": 0, "known": True})])
    await session.flush()
    await p.reconcile(session, wallet, NOW)
    assert care.state["portions"] == 0 and care.state["affinity"] == 21
    assert not care.state.get("last_meal_at")
    assert await p.counter(session, user.id, "meal") == 0
    assert await p.counter(session, user.id, "refusal") == 2

def test_preferences_positive_never_regress_and_local_day():
    class Zero:
        def random(self): return 0
    for level in (4, 5, 6):
        state = {"level": level}
        p.evolve(state, NOW, Zero())
        assert state["level"] >= level
        prior = state["level"]
        p.evolve(state, NOW + timedelta(hours=1), Zero())
        assert state["level"] == prior
    assert p.day(datetime(2026, 10, 7, 1, tzinfo=UTC)) == "2026-10-06"

def test_fishing_trace_must_interact_and_track_fish():
    state = {"speed": .3, "phase": 0., "zone": .3}
    trace = [{"t": float(i), "position": fish_position(state, i)} for i in range(21)]
    assert validate_trace("fish", state, trace)
    with pytest.raises(ValueError): validate_trace("fish", state, [{"t": float(i), "position": .5} for i in range(21)])
    with pytest.raises(ValueError): validate_trace("fish", state, trace[:10])

@pytest.mark.asyncio
async def test_cat_and_bowls_are_atomic_owned_and_return_unused_food(account):
    session, user, wallet = account
    cat=uuid4()
    session.add(RemoteCat(user_id=user.id,client_id=cat,client_updated_at=NOW,updated_by_device="test",payload={"name":"Mimi","is_owned":True}))
    await session.commit()
    await op(session,user,"add_cat",str(cat),{"anchor":[2,4],"food":[4,4],"water":[4,5]})
    care=await session.get(QuintalCatCare,(user.id,cat))
    assert not care.state["assisted"]
    care.state={**care.state,"affinity":12,"last_meal_at":p.iso(NOW),"portions":2,"recipe":"garden","served_at":p.iso(NOW)}
    await session.commit()
    await op(session,user,"remove_cat",str(cat))
    assert (await p.projection(session,user.id,NOW))["stock"]["ration:garden"]==2
    assert care.state["affinity"]==12 and care.state["last_meal_at"]==p.iso(NOW)
    for role in ("food_bowl","water_bowl"):
        from uuid import UUID
        item=await session.get(HabitatItem,UUID(care.state[role]))
        assert item.x is None
    with pytest.raises(ValueError):await op(session,user,"add_cat",str(cat),{"anchor":[2,4],"food":[4,4],"water":[4,4]})
    await session.rollback();await session.refresh(user);await session.refresh(wallet)
    assert not wallet.selected_cat_ids
    with pytest.raises(ValueError):await op(session,user,"add_cat",str(uuid4()),{"anchor":[2,4],"food":[4,4],"water":[4,5]})

@pytest.mark.asyncio
async def test_market_free_coins_keep_prices_and_product_exchange_costs(account,monkeypatch):
    from app.config import get_settings
    session,user,wallet=account
    monkeypatch.setattr(get_settings(),"habitat_free_shop",True)
    session.add(QuintalFacility(user_id=user.id,kind="market",level=1,target_level=0))
    await p.stock(session,user.id,{"corn":2,"wheat":1})
    order=await op(session,user,"order","chicken",{"payment":"coins"})
    assert wallet.balance==1000
    c=catalog()["orders"][0]
    assert c["price"]==0 and c["base_price"]==12
    await op(session,user,"collect",order["task_id"],now=NOW+timedelta(hours=1))
    await op(session,user,"order","chicken",{"payment":"products"},now=NOW+timedelta(hours=1))
    result=await p.projection(session,user.id,NOW+timedelta(hours=1))
    assert result["stock"]["corn"]==0 and result["stock"]["wheat"]==0
    assert result["stock"]["chicken"]==2

@pytest.mark.asyncio
async def test_new_residents_do_not_inherit_food_or_production(account):
    session,user,wallet=account
    resident=QuintalResident(user_id=user.id,kind="coop",state={"count":2,"animals":[],"cursor":p.iso(NOW)})
    session.add(resident)
    await p.stock(session,user.id,{"corn":6})
    await op(session,user,"feed_residents","coop")
    await op(session,user,"water_residents","coop")
    await p.projection(session,user.id,NOW+timedelta(hours=6))
    resident.state={**resident.state,"count":4}
    await p.projection(session,user.id,NOW+timedelta(hours=6))
    animals=resident.state["animals"]
    assert sum(a["ready"] for a in animals)==2
    assert all(not a.get("food_until") and a["ready"]==0 for a in animals[2:])
    await op(session,user,"feed_residents","coop",now=NOW+timedelta(hours=6))
    result=await p.projection(session,user.id,NOW+timedelta(hours=6))
    assert result["stock"]["corn"]==2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,resource,amount', [('pasture','milk',2), ('orchard','apple',3)])
async def test_individual_collection_preserves_other_resident_and_is_idempotent(account, kind, resource, amount):
    session,user,wallet=account
    ids=[str(uuid4()),str(uuid4())]
    animals=[{'id':id,'slot':slot,'ready':amount,'progress':0,'cursor':p.iso(NOW),
              'food_start':p.iso(NOW),'food_until':p.iso(NOW+timedelta(days=1))}
             for slot,id in enumerate(ids)]
    resident=QuintalResident(user_id=user.id,kind=kind,state={'count':2,'animals':animals,
        'water_start':p.iso(NOW),'water_until':p.iso(NOW+timedelta(days=1)),'ready':2*amount})
    session.add(resident)
    await session.commit()
    key=uuid4()
    first=await op(session,user,'collect_residents',kind,{'resident_id':ids[0]},key=key)
    repeated=await op(session,user,'collect_residents',kind,{'resident_id':ids[0]},key=key)
    assert first==repeated
    assert first['collected']=={resource:amount}
    snapshot=await p.projection(session,user.id,NOW)
    assert snapshot['stock'][resource]==amount
    assert snapshot['stock'].get('pear',0)==0
    state=next(r for r in snapshot['residents'] if r['kind']==kind)
    assert [a['ready'] for a in state['animals']]==[0,amount]
    assert state['ready']==amount
    assert p.utc(state['animals'][0]['next_ready_at'])==NOW+timedelta(hours=8)
    assert state['animals'][1]['next_ready_at'] is None
    assert all('next_ready_at' not in a for a in resident.state['animals'])
    with pytest.raises(ValueError,match='Ainda não há produção'):
        await op(session,user,'collect_residents',kind,{'resident_id':ids[0]})
    with pytest.raises(ValueError,match='não encontrado'):
        await op(session,user,'collect_residents',kind,{'resident_id':str(uuid4())})
    assert resident.state['animals'][1]['ready']==amount


@pytest.mark.asyncio
@pytest.mark.parametrize("practice,miss,quantity", [(False, False, 1), (True, False, 0), (False, True, 0)])
async def test_fishing_session_result_survives_reload_and_retry_without_new_credit(account, practice, miss, quantity):
    from random import Random
    from app.services.quintal_games import session_operation
    session, user, wallet = account
    user_id = user.id
    session.add(QuintalFacility(user_id=user_id, kind="pier", level=1, target_level=0))
    if practice:
        await p.counter(session, user_id, "fish_day:" + p.day(NOW), 12)
    await session.commit()
    started = await session_operation(session, user_id, "start_fish", "pier", {}, uuid4(), now=NOW, rng=Random(5))
    await session.commit()
    trace = [{"t": float(i), "position": (1 - fish_position(started, i)) if miss else fish_position(started, i)} for i in range(21)]
    key = uuid4()
    result = await session_operation(session, user_id, "finish_fish", started["session_id"], {"trace": trace}, key, now=NOW+timedelta(seconds=30))
    await session.commit()
    assert result == {"success": not miss, "quantity": quantity, "practice": practice}
    session.expire_all()
    for retry_key in (key, uuid4()):
        retry = await session_operation(session, user_id, "finish_fish", started["session_id"], {"trace": trace}, retry_key, now=NOW+timedelta(seconds=31))
        await session.commit()
        assert retry == result
    projection = await p.projection(session, user_id, now=NOW+timedelta(seconds=32))
    assert projection["stock"].get("fish", 0) == quantity
    assert await p.counter(session, user_id, "fish") == (1 if quantity else 0)


@pytest.mark.asyncio
async def test_meal_projection_tracks_each_consumption_and_server_appetite(account):
    session, user, wallet = account
    cat = uuid4()
    wallet.selected_cat_ids = [str(cat)]
    care = QuintalCatCare(user_id=user.id, cat_id=cat, state={
        'portions': 2, 'recipe': 'basic', 'served_at': p.iso(NOW), 'affinity': 0,
    })
    session.add(care)
    await session.commit()
    first = await p.projection(session, user.id, NOW)
    one = first['cats'][0]
    reaction = one['last_reaction']
    assert (reaction['portions_before'], reaction['portions_after']) == (2, 1)
    assert one['portions'] == 1
    assert p.utc(one['next_appetite_at']) == NOW + timedelta(hours=12)
    assert reaction['preference'] == reaction['next_preference'] == 3
    await session.commit()
    before = await p.projection(session, user.id, NOW + timedelta(hours=12, microseconds=-1))
    assert before['cats'][0]['last_reaction']['event_id'] == reaction['event_id']
    assert before['cats'][0]['portions'] == 1
    second = await p.projection(session, user.id, NOW + timedelta(hours=12))
    two = second['cats'][0]
    assert two['portions'] == 0
    assert (two['last_reaction']['portions_before'], two['last_reaction']['portions_after']) == (1, 0)
    assert two['last_reaction']['event_id'] != reaction['event_id']
    assert p.utc(two['next_appetite_at']) == NOW + timedelta(hours=24)
    await session.commit()
    session.expire_all()
    await session.refresh(user)
    later = await p.projection(session, user.id, NOW + timedelta(hours=25))
    assert later['cats'][0]['last_reaction'] == two['last_reaction']
    assert later['cats'][0]['hunger'] == 1
    # Deadline remains the actual past instant, never a sliding device timer.
    assert later['cats'][0]['next_appetite_at'] == two['next_appetite_at']
    assert len([r for r in later['updates'] if r['kind'] == 'meal']) == 2


@pytest.mark.asyncio
async def test_confirmed_refusal_is_receipted_once_without_satisfying_cat(account):
    session, user, wallet = account
    cat = uuid4()
    session.add(RemoteCat(user_id=user.id, client_id=cat, client_updated_at=NOW,
        updated_by_device='test', payload={'name': 'Mimi', 'is_owned': True}))
    await session.commit()
    await op(session, user, 'add_cat', str(cat), {'anchor': [2,4], 'food': [4,4], 'water': [4,5]})
    care = await session.get(QuintalCatCare, (user.id, cat))
    care.state = {**care.state, 'affinity': 12, 'last_meal_at': p.iso(NOW-timedelta(hours=1)),
                  'meal_satisfaction': 8}
    session.add(QuintalPreference(user_id=user.id, cat_id=cat, recipe='garden',
                                  state={'level': 0, 'known': True}))
    await p.stock(session, user.id, {'ration:garden': 2})
    await session.commit()
    key = uuid4()
    data = {'recipe': 'garden', 'quantity': 2, 'confirm_refusal': True}
    await op(session, user, 'serve', str(cat), data, key=key)
    state = await p.projection(session, user.id, NOW)
    reaction = state['cats'][0]['last_reaction']
    assert (reaction['portions_before'], reaction['portions_after'], reaction['lost']) == (2, 0, 2)
    shown = [r for r in state['updates'] if r['kind'] == 'refused']
    assert len(shown) == 1 and shown[0]['event_id'] == reaction['event_id']
    assert shown[0]['target'] == str(cat)
    assert care.state['affinity'] == 12 and care.state['meal_satisfaction'] == 8
    assert p.utc(state['cats'][0]['next_appetite_at']) == NOW+timedelta(hours=11)
    await op(session, user, 'serve', str(cat), data, key=key)
    replay = await p.projection(session, user.id, NOW)
    assert replay['stock']['ration:garden'] == 0
    assert replay['cats'][0]['last_reaction'] == reaction
    assert len([r for r in replay['updates'] if r['kind'] == 'refused']) == 1
    assert await p.counter(session, user.id, 'meal') == 0
    assert await p.counter(session, user.id, 'refusal') == 2


@pytest.mark.asyncio
async def test_refusals_at_same_instant_have_distinct_events_and_no_appetite_reset(account):
    session, user, wallet = account
    cat = uuid4()
    care = QuintalCatCare(user_id=user.id, cat_id=cat, state={
        'portions': 2, 'recipe': 'garden', 'served_at': p.iso(NOW), 'affinity': 0,
    })
    session.add_all([care, QuintalPreference(user_id=user.id, cat_id=cat,
        recipe='garden', state={'level': 0, 'known': True})])
    await session.flush()
    await p.meals(session, wallet, care, NOW, p.random.Random(1))
    await session.commit()
    state = await p.projection(session, user.id, NOW)
    events = [r for r in state['updates'] if r['kind'] == 'refused']
    assert len(events) == 2
    assert len({r['event_id'] for r in events}) == 2
    assert {r['at'] for r in events} == {p.iso(NOW)}
    assert {(r['portions_before'], r['portions_after']) for r in events} == {(2, 1), (1, 0)}
    assert state['cats'][0]['next_appetite_at'] is None
    assert state['cats'][0]['hunger'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,cycle", [("coop", 21600), ("pasture", 28800)])
@pytest.mark.parametrize("hours,supply", [(6, 24), (12, 24), (24, 24), (24, 8), (24, 0)])
async def test_balance_simulation_matches_authoritative_resident_cycles(account, kind, cycle, hours, supply):
    from scripts.simulate_quintal_balance import advance_resident
    session, user, wallet = account
    state = {"count": 1, "cursor": p.iso(NOW),
             "water_start": p.iso(NOW), "water_until": p.iso(NOW + timedelta(hours=supply)),
             "animals": [{"id": str(uuid4()), "slot": 0, "ready": 0, "progress": 0,
                          "cursor": p.iso(NOW), "food_start": p.iso(NOW),
                          "food_until": p.iso(NOW + timedelta(hours=supply))}]}
    resident = QuintalResident(user_id=user.id, kind=kind, state=state)
    session.add(resident)
    await session.flush()
    await p.reconcile(session, wallet, now=NOW + timedelta(hours=hours))
    expected = advance_resident(0, 0, cycle, hours * 3600, supply * 3600)
    actual = resident.state['animals'][0]
    assert (actual['ready'], actual['progress']) == expected[:2]
    assert sum(expected[2:]) == hours * 3600


@pytest.mark.parametrize("cats,visits", [(3, 1), (3, 2), (6, 1), (6, 2)])
def test_balance_scenarios_cover_full_care_without_negative_stock(cats, visits):
    from scripts.simulate_quintal_balance import run_scenario
    scenario = run_scenario(cats, visits)
    assert scenario['hens'] == 4 and scenario['cows'] == 2
    assert scenario['metrics_per_day']['meals'] == cats * 2
    assert scenario['metrics_per_day'].get('missed_meals', 0) == 0
    assert all(value >= 0 for value in scenario['minimum_available'].values())
    assert scenario['metrics_per_day']['collected_egg'] == 8 * visits
    assert scenario['metrics_per_day']['collected_milk'] == (4 if visits == 1 else 6)
    assert scenario['hours_per_day']['coop_full'] == (12 if visits == 1 else 0)
    assert scenario['estimated_taps_per_day'] > scenario['actions_per_day']['plant']


@pytest.mark.asyncio
@pytest.mark.parametrize("fruit", ["apple", "pear"])
async def test_fruit_recipe_unlock_reservation_refund_collection_and_meal(account, fruit):
    session, user, wallet = account
    session.add(QuintalFacility(user_id=user.id, kind='kitchen', level=2, target_level=0))
    await p.stock(session, user.id, {'wheat': 2, fruit: 4})
    await session.commit()
    with pytest.raises(ValueError, match='liberada'):
        await op(session, user, 'cook', fruit)
    await session.rollback(); await session.refresh(user); await session.refresh(wallet)
    session.add(QuintalFacility(user_id=user.id, kind='orchard', level=1, target_level=0))
    key = uuid4()
    first = await op(session, user, 'cook', fruit, key=key)
    assert await op(session, user, 'cook', fruit, key=key) == first
    second = await op(session, user, 'cook', fruit)
    before = await p.projection(session, user.id, NOW)
    assert before['stock']['wheat'] == before['stock'][fruit] == 0
    cancel_key = uuid4()
    await op(session, user, 'cancel', second['task_id'], key=cancel_key)
    await op(session, user, 'cancel', second['task_id'], key=cancel_key)
    at = NOW + timedelta(minutes=20)
    ready = await p.projection(session, user.id, at)
    assert ready['stock']['wheat'] == 1 and ready['stock'][fruit] == 2
    assert ready['stock'].get('ration:' + fruit, 0) == 0
    collect_key = uuid4()
    await op(session, user, 'collect', first['task_id'], now=at, key=collect_key)
    await op(session, user, 'collect', first['task_id'], now=at, key=collect_key)
    assert (await p.projection(session, user.id, at))['stock']['ration:' + fruit] == 3
    cat = uuid4()
    session.add(RemoteCat(user_id=user.id, client_id=cat, client_updated_at=at,
        updated_by_device='test', payload={'name': 'Mimi', 'is_owned': True}))
    session.add(QuintalPreference(user_id=user.id, cat_id=cat, recipe=fruit,
                                 state={'level': 3, 'known': True}))
    await session.commit()
    await op(session, user, 'add_cat', str(cat), {'anchor': [2,4], 'food': [4,4], 'water': [4,5]}, now=at)
    serve_key = uuid4()
    await op(session, user, 'serve', str(cat), {'recipe': fruit, 'quantity': 2}, now=at, key=serve_key)
    await op(session, user, 'serve', str(cat), {'recipe': fruit, 'quantity': 2}, now=at, key=serve_key)
    final = await p.projection(session, user.id, at)
    assert final['stock']['ration:' + fruit] == 1
    care = next(c for c in final['cats'] if c['cat_id'] == str(cat))
    assert care['recipe'] == fruit and care['portions'] == 1
    assert care['meal_satisfaction'] == 16


@pytest.mark.asyncio
async def test_fruit_trade_keeps_coin_price_and_existing_trade_with_replay(account, monkeypatch):
    session, user, wallet = account
    monkeypatch.setattr(p, 'effective_price', lambda base: base)
    session.add_all([QuintalFacility(user_id=user.id, kind=k, level=1, target_level=0)
                     for k in ('market', 'orchard')])
    await p.stock(session, user.id, {'apple': 3, 'pear': 3, 'corn': 2, 'wheat': 1})
    key = uuid4()
    payload = {'payment': 'products', 'product_option': 'fruit'}
    order = await op(session, user, 'order', 'chicken', payload, key=key)
    await op(session, user, 'order', 'chicken', payload, key=key)
    pending = await p.projection(session, user.id, NOW)
    assert pending['stock']['apple'] == pending['stock']['pear'] == 0
    assert pending['stock']['corn'] == 2 and pending['stock']['wheat'] == 1
    assert wallet.balance == 1000
    assert pending['tasks'][0]['inputs'] == {'apple': 3, 'pear': 3}
    assert pending['tasks'][0]['outputs'] == {'chicken': 2}
    at = NOW + timedelta(hours=1)
    assert (await p.projection(session, user.id, at))['stock'].get('chicken', 0) == 0
    collect_key = uuid4()
    await op(session, user, 'collect', order['task_id'], now=at, key=collect_key)
    await op(session, user, 'collect', order['task_id'], now=at, key=collect_key)
    assert (await p.projection(session, user.id, at))['stock']['chicken'] == 2
    # The old request shape still uses corn/wheat, even after adding fruit options.
    original = await op(session, user, 'order', 'chicken', {'payment': 'products'}, now=at)
    await op(session, user, 'collect', original['task_id'], now=at+timedelta(hours=1))
    await op(session, user, 'order', 'chicken', {'payment': 'coins'}, now=at+timedelta(hours=1))
    assert wallet.balance == 988
    c = next(o for o in catalog()['orders'] if o['id'] == 'chicken')
    assert c['base_price'] == 12 and c['products'] == {'corn': 2, 'wheat': 1}


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,orchard', [
    ({'payment': 'products', 'product_option': 'fruit'}, False),
    ({'payment': 'products', 'product_option': 'unknown'}, True),
    ({'payment': 'coins', 'product_option': 'fruit'}, True),
])
async def test_invalid_or_locked_fruit_trade_cannot_spend(account, payload, orchard):
    session, user, wallet = account
    session.add(QuintalFacility(user_id=user.id, kind='market', level=1, target_level=0))
    if orchard: session.add(QuintalFacility(user_id=user.id, kind='orchard', level=1, target_level=0))
    await p.stock(session, user.id, {'apple': 3, 'pear': 3})
    await session.commit()
    with pytest.raises(ValueError): await op(session, user, 'order', 'chicken', payload)
    await session.rollback(); await session.refresh(user); await session.refresh(wallet)
    stock = (await p.projection(session, user.id, NOW))['stock']
    assert stock['apple'] == stock['pear'] == 3 and wallet.balance == 1000


@pytest.mark.asyncio
async def test_market_quantity_scales_ingredients_output_and_deadline(account):
    session, user, wallet = account
    session.add(QuintalFacility(user_id=user.id, kind='market', level=1))
    await p.stock(session, user.id, {'milk': 3, 'wheat': 10})
    await session.commit()
    with pytest.raises(ValueError, match='insuficientes'):
        await op(session, user, 'order', 'beef', {'payment': 'products', 'quantity': 4})
    assert (await p.projection(session, user.id, NOW))['stock']['milk'] == 3
    assert not await p.rows(session, QuintalTask, user.id)
    await p.stock(session, user.id, {'milk': 1})
    result = await op(session, user, 'order', 'beef', {'payment': 'products', 'quantity': 4})
    task = await session.get(QuintalTask, p.UUID(result['task_id']))
    assert task.inputs == {'milk': 4, 'wheat': 4}
    assert task.outputs == {'beef': 4}
    assert p.utc(task.ready_at) - p.utc(task.started_at) == timedelta(hours=4)
    assert (await p.projection(session, user.id, NOW))['stock']['milk'] == 0


@pytest.mark.asyncio
async def test_pair_reuses_legacy_bowls_and_recovers_lost_references(account):
    session, user, wallet = account
    cat = uuid4()
    wallet.selected_cat_ids = [str(cat)]
    first = HabitatItem(id=uuid4(), user_id=user.id, sku='food_bowl', purchase_key='legacy-food')
    second = HabitatItem(id=uuid4(), user_id=user.id, sku='water_bowl', purchase_key='legacy-water')
    session.add_all([first, second])
    await session.flush()
    care = await p.ensure_pair(session, wallet, cat)
    assert care.state['food_bowl'] == str(first.id)
    assert care.state['water_bowl'] == str(second.id)
    care.state = {'affinity': 9}
    await session.flush()
    for _ in range(3):
        await p.ensure_pair(session, wallet, cat)
    assert len(await p.rows(session, HabitatItem, user.id)) == 2
    assert care.state['affinity'] == 9
    assert care.state['food_bowl'] == str(first.id)


def test_play_accepts_vertical_motion_and_rejects_invalid_y():
    trace = [{'t': float(i), 'position': .5, 'y': [.1, .5, .9][i % 3]} for i in range(21)]
    assert validate_trace('play', {}, trace)
    trace[3]['y'] = float('nan')
    with pytest.raises(ValueError, match='vertical'):
        validate_trace('play', {}, trace)


@pytest.mark.asyncio
async def test_notifications_cursor_preserves_timestamp_ties_and_owner(account):
    from app.persistence_models import AppNotification
    from app.routers.vision import notifications
    session, user, wallet = account
    connection = await session.connection()
    await connection.run_sync(AppNotification.__table__.create)
    other = User(id=uuid4(), email='other-inbox@test.local', status='active')
    session.add(other)
    await session.flush()
    ids = [uuid4() for _ in range(5)]
    session.add_all([AppNotification(id=id, user_id=user.id, type='achievement', payload={}, created_at=NOW) for id in ids])
    session.add(AppNotification(user_id=other.id, type='message', payload={}, created_at=NOW))
    await session.commit()
    first = await notifications(limit=2, cursor=None, cursor_id=None, user=user, session=session)
    second = await notifications(limit=2, cursor=first[-1]['created_at'], cursor_id=p.UUID(first[-1]['id']), user=user, session=session)
    third = await notifications(limit=2, cursor=second[-1]['created_at'], cursor_id=p.UUID(second[-1]['id']), user=user, session=session)
    assert [row['id'] for row in first + second + third] == sorted(map(str,ids), reverse=True)
