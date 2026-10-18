"""Opt-in loopback HTTP tests using disposable accounts in the running local DB.

QUINTAL_LIVE_API_TEST=1; QUINTAL_LIVE_API_URL defaults to localhost:8000.
No email, player credentials, app restart, global flags or device clock changes.
Only the UUID-scoped accounts created here are deleted. Timed ready states are
seeded in those accounts explicitly; this is not proof of a physical device.
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import get_settings
from app.persistence_models import User, HabitatWallet, RemoteCat, HabitatItem
from app.quintal_models import (QuintalFacility, QuintalResource, QuintalTask,
    QuintalCounter, QuintalPlot, QuintalResident, QuintalCatCare, QuintalPreference)
from app.services.auth_service import issue_tokens
from app.services.quintal_production import iso

pytestmark = pytest.mark.skipif(os.getenv("QUINTAL_LIVE_API_TEST") != "1",
                                reason="Explicit live API opt-in required")


@pytest_asyncio.fixture
async def live():
    url = os.getenv("QUINTAL_LIVE_API_URL", "http://127.0.0.1:8000")
    db_url = get_settings().database_url
    assert urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"}
    assert make_url(db_url).host in {"127.0.0.1", "localhost", "::1"}
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = [uuid4(), uuid4()]
    emails = [f"quintal-live-{uid.hex}@example.invalid" for uid in ids]
    clients = []
    try:
        async with factory() as db:
            users = [User(id=uid, email=email, status="active") for uid, email in zip(ids, emails)]
            db.add_all(users)
            await db.flush()
            for user in users:
                db.add(HabitatWallet(user_id=user.id, balance=1000, expansion_level=3,
                                     selected_cat_ids=[], care_state={}))
                for kind, level in (("market", 1), ("kitchen", 2), ("orchard", 1), ("coop", 2), ("pasture", 2)):
                    db.add(QuintalFacility(user_id=user.id, kind=kind, level=level, target_level=0))
                for resource, quantity in {"wheat": 30, "corn": 30, "apple": 9, "pear": 9,
                                           "carrot": 10, "ration:garden": 6}.items():
                    db.add(QuintalResource(user_id=user.id, resource=resource, quantity=quantity))
                db.add(QuintalCounter(user_id=user.id, metric="care", quantity=1, projected_quantity=1))
            tokens = [await issue_tokens(db, user=users[index], device_id=f"quintal-live-client-{i}")
                      for i, index in enumerate((0, 0, 1))]
            await db.commit()
        clients = [httpx.AsyncClient(base_url=url, timeout=30,
                   headers={"Authorization": f"Bearer {token['access_token']}"}) for token in tokens]
        for client in clients:
            response = await client.get("/app/quintal")
            assert response.status_code == 200, f"Running API/DB compatibility: HTTP {response.status_code}"
        yield SimpleNamespace(db=factory, uid=ids[0], other_uid=ids[1],
                              a=clients[0], b=clients[1], other=clients[2], url=url)
    finally:
        for client in clients:
            await client.aclose()
        # Exact UUID AND generated email: no existing account can match this cleanup.
        async with factory() as db:
            await db.execute(delete(User).where(User.id.in_(ids), User.email.in_(emails)))
            await db.commit()
            assert not (await db.scalars(select(User.id).where(User.id.in_(ids)))).all()
        await engine.dispose()


def command(action, target, data=None, key=None):
    return {"action": action, "target": target, "data": data or {},
            "idempotency_key": key or str(uuid4())}


async def send(client, body, expected=200):
    response = await client.post("/app/quintal/operations", json=body)
    assert response.status_code == expected, f"{body['action']}: HTTP {response.status_code}: {response.text}"
    return response.json()


async def snapshot(client):
    response = await client.get("/app/quintal")
    assert response.status_code == 200
    return response.json()["production"]


@pytest.mark.asyncio
async def test_live_fruit_order_two_sessions_lost_response_and_collection(live):
    request = command("order", "chicken", {"payment": "products", "product_option": "fruit"})
    first, second = await asyncio.gather(send(live.a, request), send(live.b, request))
    assert first["outcome"] == second["outcome"]
    task = first["outcome"]["task_id"]
    assert first["production"]["stock"]["apple"] == 6
    assert first["production"]["stock"]["pear"] == 6
    assert len(first["production"]["tasks"]) == 1
    # Close a transport after commit, discard its response and reconnect with the same operation.
    async with httpx.AsyncClient(base_url=live.url, headers=live.a.headers, timeout=30) as restarted:
        replay = await send(restarted, request)
        assert replay["outcome"] == first["outcome"]
        assert replay["production"]["stock"]["apple"] == 6
    await send(live.b, {**request, "data": {"payment": "coins"}}, expected=409)
    await send(live.other, command("collect", task), expected=409)
    now = datetime.now(UTC)
    async with live.db() as db:
        row = await db.get(QuintalTask, UUID(task))
        assert row.user_id == live.uid
        row.started_at, row.ready_at = now - timedelta(hours=2), now - timedelta(hours=1)
        await db.commit()
    ready = await snapshot(live.b)
    assert ready["stock"].get("chicken", 0) == 0  # deadline is not collection
    requests = [command("collect", task), command("collect", task)]
    responses = await asyncio.gather(*[client.post("/app/quintal/operations", json=body)
                                      for client, body in zip((live.a, live.b), requests)])
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
    replay = await send(live.b, requests[winner])
    assert replay["production"]["stock"]["chicken"] == 2
    assert (await snapshot(live.other))["stock"].get("chicken", 0) == 0


@pytest.mark.asyncio
async def test_live_build_plant_water_cook_queue_cancel_and_collect(live):
    built = await send(live.a, command("build", "garden:1"))
    assert not built["production"]["plots"]
    finish = command("finish_build", "garden")
    finished = await send(live.b, finish)
    assert len(finished["production"]["plots"]) == 4
    assert len((await send(live.a, finish))["production"]["plots"]) == 4
    planted = await send(live.a, command("plant", "0", {"crop": "wheat"}))
    deadline = planted["production"]["plots"][0]["ready_at"]
    water = command("water_crop", "0")
    watered = await send(live.b, water)
    assert watered["production"]["plots"][0]["ready_at"] < deadline
    await send(live.a, water)
    await send(live.a, command("harvest", "0"), expected=409)
    async with live.db() as db:
        plot = await db.get(QuintalPlot, (live.uid, 0))
        plot.ready_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    ready = await snapshot(live.b)
    assert ready["stock"]["wheat"] == 30
    harvest = command("harvest", "0")
    harvested = await send(live.a, harvest)
    after_harvest = harvested["production"]["stock"]["wheat"]
    assert after_harvest > 30
    assert (await send(live.b, harvest))["production"]["stock"]["wheat"] == after_harvest
    first = await send(live.a, command("cook", "apple"))
    queued = await send(live.b, command("cook", "pear"))
    cancel = command("cancel", queued["outcome"]["task_id"])
    cancelled = await send(live.a, cancel)
    assert cancelled["production"]["stock"]["pear"] == 9
    assert cancelled["production"]["stock"]["apple"] == 7
    assert cancelled["production"]["stock"]["wheat"] == after_harvest - 1
    assert (await send(live.b, cancel))["production"]["stock"] == cancelled["production"]["stock"]
    task_id = first["outcome"]["task_id"]
    async with live.db() as db:
        task = await db.get(QuintalTask, UUID(task_id))
        task.started_at = datetime.now(UTC) - timedelta(hours=1)
        task.ready_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
    assert (await snapshot(live.a))["stock"].get("ration:apple", 0) == 0
    collect = command("collect", task_id)
    for client in (live.a, live.b):
        assert (await send(client, collect))["production"]["stock"]["ration:apple"] == 3
    progress = await live.a.get("/app/progress")
    assert progress.status_code == 200
    routine = next(m for m in progress.json()["daily_missions"] if m.get("group") == "quintal_routine")
    assert routine["completed"] and routine["reward_received"]


@pytest.mark.asyncio
async def test_live_refusal_removal_and_individual_resident_collection(live):
    now = datetime.now(UTC)
    cat = uuid4()
    async with live.db() as db:
        db.add(RemoteCat(user_id=live.uid, client_id=cat, client_updated_at=now,
                        updated_by_device="quintal-live", payload={"name": "Gato de validação", "is_owned": True}))
        await db.commit()
    await send(live.a, command("add_cat", str(cat), {"anchor": [2,4], "food": [4,4], "water": [4,5]}))
    async with live.db() as db:
        care = await db.get(QuintalCatCare, (live.uid, cat))
        care.state = {**care.state, "affinity": 12, "last_meal_at": iso(now - timedelta(hours=1)), "meal_satisfaction": 8}
        db.add(QuintalPreference(user_id=live.uid, cat_id=cat, recipe="garden", state={"level": 0, "known": True}))
        await db.commit()
    await send(live.a, command("serve", str(cat), {"recipe": "garden", "quantity": 2}), expected=409)
    refuse = command("serve", str(cat), {"recipe": "garden", "quantity": 2, "confirm_refusal": True})
    for client in (live.a, live.b):
        result = await send(client, refuse)
        assert result["production"]["stock"]["ration:garden"] == 4
        state = next(r for r in result["production"]["cats"] if r["cat_id"] == str(cat))
        assert state["affinity"] == 12 and state["portions"] == 0
        assert state["last_reaction"]["kind"] == "refused"
    await send(live.b, command("remove_cat", str(cat)))
    async with live.db() as db:
        care = await db.get(QuintalCatCare, (live.uid, cat))
        assert care.state["affinity"] == 12
        assert not (await db.get(HabitatWallet, live.uid)).selected_cat_ids
        for role in ("food_bowl", "water_bowl"):
            assert (await db.get(HabitatItem, UUID(care.state[role]))).x is None
    for kind, resource, amount in (("orchard", "apple", 3), ("pasture", "milk", 2)):
        resident_ids = [str(uuid4()), str(uuid4())]
        async with live.db() as db:
            row = await db.get(QuintalResident, (live.uid, kind))
            if row is None:
                row = QuintalResident(user_id=live.uid, kind=kind, state={})
                db.add(row)
            row.state = {"count": 2, "cursor": iso(now), "ready": amount * 2,
                         "animals": [{"id": rid, "slot": i, "ready": amount, "progress": 0, "cursor": iso(now)}
                                     for i, rid in enumerate(resident_ids)]}
            await db.commit()
        before = (await snapshot(live.a))["stock"].get(resource, 0)
        collect = command("collect_residents", kind, {"resident_id": resident_ids[0]})
        for client in (live.a, live.b):
            result = await send(client, collect)
            assert result["production"]["stock"][resource] == before + amount
            row = next(r for r in result["production"]["residents"] if r["kind"] == kind)
            assert [a["ready"] for a in row["animals"]] == [0, amount]
        await send(live.other, command("collect_residents", kind, {"resident_id": resident_ids[0]}), expected=409)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "miss", "practice", "play"])
async def test_live_game_result_uses_real_server_elapsed_time_and_replays_once(live, mode):
    from app.services.quintal_games import fish_position
    from app.services.quintal_production import day
    now = datetime.now(UTC)
    cat = uuid4()
    async with live.db() as db:
        db.add_all([QuintalFacility(user_id=live.uid, kind="pier", level=1, target_level=0),
                    QuintalFacility(user_id=live.uid, kind="rod", level=1, target_level=0)])
        if mode == "practice":
            db.add(QuintalCounter(user_id=live.uid, metric="fish_day:" + day(now), quantity=12, projected_quantity=0))
        if mode == "play":
            db.add(RemoteCat(user_id=live.uid, client_id=cat, client_updated_at=now,
                            updated_by_device="quintal-live", payload={"name": "Gato da brincadeira", "is_owned": True}))
        await db.commit()
    if mode == "play":
        await send(live.a, command("add_cat", str(cat), {"anchor": [2,4], "food": [4,4], "water": [4,5]}))
    kind, target = ("play", str(cat)) if mode == "play" else ("fish", "pier")
    start_request = command("start_" + kind, target)
    game = (await send(live.a, start_request))["outcome"]
    assert (await send(live.b, start_request))["outcome"] == game
    if mode == "miss":
        abandoned = game
        game = (await send(live.b, command("start_fish", "pier")))["outcome"]
        previous = (await send(live.a, command("finish_fish", abandoned["session_id"])))["outcome"]
        assert previous == {"success": False, "quantity": 0, "abandoned": True}
    trace = [{"t": float(i), "position": [.2, .5, .8][i % 3] if mode == "play"
              else (1 - fish_position(game, i) if mode == "miss" else fish_position(game, i))}
             for i in range(16)]
    finish_request = command("finish_" + kind, game["session_id"], {"trace": trace})
    await send(live.other, finish_request, expected=409)
    await send(live.a, finish_request, expected=409)  # immediate submit cannot bypass elapsed time
    # Real waiting on the server: no edits to the session or machine clock.
    await asyncio.sleep(15.2 + (game["bite_seconds"] if kind == "fish" else 0))
    finished = await send(live.a, finish_request)
    result = finished["outcome"]
    assert result["success"] == (mode != "miss")
    assert result["practice"] == (mode == "practice")
    assert result["quantity"] == (1 if mode == "success" else 0)
    before = finished["production"]["stock"].get("fish", 0)
    assert before == (1 if mode == "success" else 0)
    if mode == "play":
        assert result["care_effect"]["bond_gained"] == 2
        assert result["care_effect"]["play_bonus_after"] > result["care_effect"]["play_bonus_before"]
    # Same and new idempotency keys cannot mint a second reward for this session.
    for request in (finish_request, {**finish_request, "idempotency_key": str(uuid4())}):
        replay = await send(live.b, request)
        assert replay["outcome"] == result
        assert replay["production"]["stock"].get("fish", 0) == before
        if mode == "play":
            cat_state = next(c for c in replay["production"]["cats"] if c["cat_id"] == str(cat))
            assert cat_state["affinity"] == result["care_effect"]["bond_total"]

@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('QUINTAL_LIVE_FLUTTER_TEST') != '1',
                    reason='Explicit live Flutter process opt-in required')
async def test_flutter_process_restart_after_socket_loss_and_account_switch(live):
    import json
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / 'catlogue'
    flutter = Path(os.getenv('FLUTTER_BIN', r'C:\flutter\bin\flutter.bat'))
    assert flutter.is_file() and (root / 'test/quintal_live_client_test.dart').is_file()
    storage = root / 'build' / ('quintal-live-client-' + uuid4().hex)
    storage.mkdir(parents=True)
    dropped = False
    operations = []
    errors = []
    async with httpx.AsyncClient(base_url=live.url, timeout=30) as upstream:
        async def proxy(reader, writer):
            nonlocal dropped
            try:
                head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),30)
                lines = head.decode('latin-1').split('\r\n')
                method, path, _ = lines[0].split(' ',2)
                headers = dict(line.split(':',1) for line in lines[1:] if ':' in line)
                headers = {key.lower(): value.strip() for key,value in headers.items()}
                body = await reader.readexactly(int(headers.get('content-length','0')))
                forwarded = {key:value for key,value in headers.items()
                             if key not in {'host','connection','content-length','accept-encoding'}}
                response = await upstream.request(method,path,headers=forwarded,content=body)
                if path == '/app/quintal/operations':
                    command_body = json.loads(body)
                    operations.append((command_body['action'],command_body['idempotency_key']))
                    if command_body['action']=='order' and not dropped:
                        assert response.status_code == 200
                        dropped=True
                        # PostgreSQL has committed. Close the real TCP socket
                        # without delivering any HTTP response to Flutter.
                        return
                payload = response.content
                writer.write((f'HTTP/1.1 {response.status_code} OK\r\n'
                              f'Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n'
                              'Connection: close\r\n\r\n').encode()+payload)
                await writer.drain()
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(proxy,'127.0.0.1',0)
        port = server.sockets[0].getsockname()[1]
        try:
            for phase, client in [('before',live.a),('after',live.b)]:
                options = {'phase':phase,'database':str(storage),
                           'account':{'id':str(live.uid),'token':client.headers['authorization'].removeprefix('Bearer ')},
                           'other':{'id':str(live.other_uid),'token':live.other.headers['authorization'].removeprefix('Bearer ')}}
                env = {**os.environ,'QUINTAL_LIVE_CLIENT':json.dumps(options)}
                result = await asyncio.to_thread(subprocess.run,
                    ['cmd.exe','/d','/c',str(flutter),'test','--no-pub',
                     f'--dart-define=API_BASE_URL=http://127.0.0.1:{port}',
                     'test/quintal_live_client_test.dart'],
                    cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                    timeout=420,creationflags=subprocess.CREATE_NO_WINDOW)
                log = root / 'build' / f'quintal-live-client-{phase}.log'
                log.write_bytes(result.stdout)
                assert result.returncode == 0, f'Flutter {phase} failed; inspect {log.name}'
            assert dropped and not errors, errors
            assert len(operations)==2 and operations[0]==operations[1]
            state = await snapshot(live.a)
            assert state['stock']['apple']==6 and state['stock']['pear']==6
            assert len(state['tasks'])==1
            other = await snapshot(live.other)
            assert other['stock']['apple']==9 and not other['tasks']
        finally:
            server.close()
            await server.wait_closed()
    # SQLite evidence stays under build; it contains only these disposable
    # accounts' production snapshots, never access/refresh tokens.

@pytest.mark.asyncio
@pytest.mark.parametrize('initial_affinity',[12,19])
async def test_live_pet_projection_and_bond_receipt_survive_reconnect(live,initial_affinity):
    now, cat = datetime.now(UTC), uuid4()
    async with live.db() as db:
        db.add(RemoteCat(user_id=live.uid,client_id=cat,client_updated_at=now,
                        updated_by_device='quintal-live',payload={'name':'Gato do carinho','is_owned':True}))
        await db.commit()
    await send(live.a,command('add_cat',str(cat),{'anchor':[2,4],'food':[4,4],'water':[4,5]}))
    async with live.db() as db:
        care = await db.get(QuintalCatCare,(live.uid,cat))
        care.state = {**care.state,'affinity':initial_affinity}
        wallet = await db.get(HabitatWallet,live.uid)
        wallet.care_state = {**wallet.care_state,'cats':{str(cat):{'affinity':initial_affinity}}}
        await db.commit()
    body = {'action':'pet','target_id':str(cat),'idempotency_key':str(uuid4())}
    responses = await asyncio.gather(*[client.post('/app/habitat/interactions',json=body)
                                      for client in (live.a,live.b)])
    for response in responses:
        assert response.status_code==200
        result=response.json()
        assert result['care']['cats'][str(cat)]['affinity']==initial_affinity+1
        assert next(c for c in result['production']['cats'] if c['cat_id']==str(cat))['affinity']==initial_affinity+1
    async with httpx.AsyncClient(base_url=live.url,headers=live.a.headers,timeout=30) as restarted:
        refreshed=(await restarted.get('/app/quintal')).json()
        assert next(c for c in refreshed['production']['cats'] if c['cat_id']==str(cat))['affinity']==initial_affinity+1
        if initial_affinity==19:
            receipts=refreshed['progress']['celebrations']
            bond=[r for r in receipts if r['kind']=='bond']
            assert len(bond)==1 and bond[0]['threshold']==20
            assert bond[0]['items'][0]['sku']=='friendship_garden'
            assert sum(i['sku']=='friendship_garden' for i in refreshed['habitat']['items'])==1
            await send(restarted,command('ack_rewards','quintal',{'event_ids':[r['id'] for r in receipts]}))
        replay=await restarted.post('/app/habitat/interactions',json=body)
        assert replay.status_code==200
        assert replay.json()['care']['cats'][str(cat)]['affinity']==initial_affinity+1
        if initial_affinity==19:
            assert not replay.json()['production_progress'].get('celebrations')
            assert sum(i['sku']=='friendship_garden' for i in replay.json()['items'])==1
    async with live.db() as db:
        assert (await db.get(QuintalCatCare,(live.uid,cat))).state['affinity']==initial_affinity+1
