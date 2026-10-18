"""HTTP contract against a disposable DB; never changes a test player's progress."""
from uuid import uuid4
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import Base,get_database_session
from app.persistence_models import User,HabitatWallet
from app.quintal_models import QuintalFacility
from app.routers.auth import current_user
from app.routers.quintal import router
from app.config import get_settings
from datetime import UTC, datetime
from app.persistence_models import RemoteCat
from app.quintal_models import QuintalCatCare
from app.routers.habitat import router as habitat_router

@pytest.mark.asyncio
async def test_authenticated_projection_mutation_validation_and_replay(monkeypatch):
    monkeypatch.setattr(get_settings(),'habitat_free_shop',True)
    monkeypatch.setattr(get_settings(),'progression_rollout_percent',100)
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        user=User(id=uuid4(),email='quintal-http@test.local',status='active')
        db.add_all([user,HabitatWallet(user_id=user.id,balance=21,care_state={},selected_cat_ids=[],expansion_level=0),QuintalFacility(user_id=user.id,kind='market',level=1,target_level=0)])
        await db.commit()
        app=FastAPI();app.include_router(router)
        async def authenticated():return user
        async def database():yield db
        app.dependency_overrides[current_user]=authenticated
        app.dependency_overrides[get_database_session]=database
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            initial=await client.get('/app/quintal')
            assert initial.status_code==200,initial.text
            assert initial.json()['production']['catalog']['orders'][0]['base_price']==12
            key=str(uuid4())
            request={'action':'order','target':'chicken','data':{'payment':'coins'},'idempotency_key':key}
            created=await client.post('/app/quintal/operations',json=request)
            assert created.status_code==200,created.text
            replay=await client.post('/app/quintal/operations',json=request)
            assert replay.status_code==200 and replay.json()['outcome']==created.json()['outcome']
            assert replay.json()['habitat']['balance']==21
            assert len(replay.json()['production']['tasks'])==1
            request['data']={'payment':'products'}
            rejected=await client.post('/app/quintal/operations',json=request)
            assert rejected.status_code==409
            malformed=await client.post('/app/quintal/operations',json={**request,'action':'water_cat','data':{'quantity':True}})
            assert malformed.status_code==422
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_affinity", [12, 19])
async def test_pet_response_contains_current_production_and_replay_keeps_one_point(monkeypatch, initial_affinity):
    monkeypatch.setattr(get_settings(), 'progression_rollout_percent', 100)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        user = User(id=uuid4(), email='care-projection@test.local', status='active')
        cat = uuid4()
        db.add_all([
            user,
            HabitatWallet(user_id=user.id, balance=0, care_state={}, selected_cat_ids=[], expansion_level=0),
            RemoteCat(user_id=user.id, client_id=cat, client_updated_at=datetime.now(UTC),
                      updated_by_device='test', payload={'name': 'Mimi', 'is_owned': True}),
        ])
        await db.commit()
        app = FastAPI()
        app.include_router(router)
        app.include_router(habitat_router)
        async def authenticated(): return user
        async def database(): yield db
        app.dependency_overrides[current_user] = authenticated
        app.dependency_overrides[get_database_session] = database
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            placed = await client.post('/app/quintal/operations', json={
                'action': 'add_cat', 'target': str(cat), 'idempotency_key': str(uuid4()),
                'data': {'anchor': [2,4], 'food': [4,4], 'water': [4,5]},
            })
            assert placed.status_code == 200, placed.text
            care = await db.get(QuintalCatCare, (user.id, cat))
            care.state = {**care.state, 'affinity': initial_affinity}
            await db.commit()
            request = {'action': 'pet', 'target_id': str(cat), 'idempotency_key': str(uuid4())}
            for _ in range(2):
                response = await client.post('/app/habitat/interactions', json=request)
                assert response.status_code == 200, response.text
                body = response.json()
                assert body['production']['cats'][0]['affinity'] == initial_affinity + 1
                assert body['care']['cats'][str(cat)]['affinity'] == initial_affinity + 1
            refreshed = await client.get('/app/quintal')
            assert refreshed.json()['production']['cats'][0]['affinity'] == initial_affinity + 1
            if initial_affinity == 19:
                receipts = body['production_progress']['celebrations']
                bond = [r for r in receipts if r['kind'] == 'bond']
                assert len(bond) == 1 and bond[0]['threshold'] == 20
                assert bond[0]['items'][0]['sku'] == 'friendship_garden'
                assert sum(i['sku'] == 'friendship_garden' for i in body['items']) == 1
                assert any(r['id'] == bond[0]['id'] for r in refreshed.json()['progress']['celebrations'])
                acknowledged = await client.post('/app/quintal/operations', json={
                    'action': 'ack_rewards', 'target': 'quintal', 'idempotency_key': str(uuid4()),
                    'data': {'event_ids': [r['id'] for r in receipts]},
                })
                assert acknowledged.status_code == 200, acknowledged.text
                assert not acknowledged.json()['progress'].get('celebrations')
                replay = await client.post('/app/habitat/interactions', json=request)
                assert not replay.json()['production_progress'].get('celebrations')
                assert sum(i['sku'] == 'friendship_garden' for i in replay.json()['items']) == 1

    await engine.dispose()


@pytest.mark.parametrize("option", ["fruit", "harvest"])
def test_order_contract_accepts_named_product_option(option):
    from app.routers.quintal import OperationRequest
    body = OperationRequest(action="order", target="chicken", idempotency_key=uuid4(),
                            data={"payment": "products", "product_option": option})
    assert body.data["product_option"] == option


@pytest.mark.parametrize("option", [True, 1, [], "x" * 65])
def test_order_contract_rejects_malformed_product_option(option):
    from app.routers.quintal import OperationRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        OperationRequest(action="order", target="chicken", idempotency_key=uuid4(),
                         data={"payment": "products", "product_option": option})
