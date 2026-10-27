"""Authorized long load on disposable API/DB; no product or device operations."""

import asyncio
from collections import Counter
from contextvars import ContextVar
import json
import logging
import os
from pathlib import Path
import sys
import time

import httpx
import pytest
from sqlalchemy import event, func, select

pytest_plugins = ["current_offline_cases"]
from security_cases import NOW, evidence, uid
from measurements import resources
from subprocesses import run_logged
from app import main
from app.persistence_models import (
    User, HabitatWallet, HabitatTransaction, RemoteCat, SyncChange, Conversation,
    ConversationMember, ConversationMessage, FeedItem, ProductState, VisionJob,
)
from app.services.auth_service import issue_tokens

ROUTE = ContextVar("quality_load_route", default=("setup", "none"))


@pytest.fixture(autouse=True)
def observe_app(monkeypatch):
    app = main.app
    state = {"phase": "setup", "http": Counter(), "sql": Counter(), "active": 0, "peak_active": 0}

    class Observed:
        def __getattr__(self, name):
            return getattr(app, name)

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            phase, path = state["phase"], scope["path"]
            context = ROUTE.set((phase, path))
            state["active"] += 1
            state["peak_active"] = max(state["peak_active"], state["active"])

            async def record(message):
                if message["type"] == "http.response.start":
                    state["http"][(phase, path, message["status"])] += 1
                await send(message)
            try:
                await app(scope, receive, record)
            finally:
                state["active"] -= 1
                ROUTE.reset(context)
    monkeypatch.setattr(main, "app", Observed())
    return state


async def seed(own_api):
    actors = []
    async with own_api.factory() as session:
        peer = User(id=uid("load-peer"), email="load-peer@example.com", username="load_peer", status="active")
        session.add(peer)
        await session.flush()
        session.add(ProductState(user_id=peer.id, preferences={"gallery_audience": "public"}))
        for index in range(100):
            session.add(FeedItem(id=uid(f"load-feed-{index}"), user_id=peer.id,
                type="cat_post", payload={"is_public": True}, created_at=NOW))
        for index in range(50):
            user = User(id=uid(f"load-user-{index}"), email=f"load-{index}@example.com",
                username=f"load_{index:02}", status="active")
            session.add(user)
            await session.flush()
            session.add(HabitatWallet(user_id=user.id, balance=1000000))
            bundle = await issue_tokens(session, user=user, device_id="long-load-fixture")
            cat_ids = []
            for count in range(5):
                ident = uid(f"load-cat-{index}-{count}")
                client = uid(f"load-client-{index}-{count}")
                session.add(RemoteCat(id=ident, user_id=user.id, client_id=client,
                    payload={"name": f"Fixture {count}", "is_owned": True},
                    client_updated_at=NOW, updated_by_device="long-load-fixture"))
                await session.flush()
                session.add(SyncChange(user_id=user.id, cat_id=ident))
                cat_ids.append(str(client))
            conversation = uid(f"load-conversation-{index}")
            session.add(Conversation(id=conversation))
            await session.flush()
            session.add_all([ConversationMember(conversation_id=conversation, user_id=owner) for owner in (user.id, peer.id)])
            session.add(ConversationMessage(conversation_id=conversation, sender_id=peer.id, body="synthetic load fixture", created_at=NOW))
            actors.append({"user_id": str(user.id), "access_token": bundle["access_token"],
                "refresh_token": bundle["refresh_token"], "expires_at": time.time()+bundle["expires_in"],
                "cat_ids": cat_ids, "conversation_id": str(conversation)})
        await session.commit()
    async with httpx.AsyncClient(base_url=own_api.url, timeout=20) as client:
        for actor in actors:
            headers = {"Authorization": "Bearer " + actor["access_token"]}
            for name, path in (("pull", "/sync/pull"), ("inbox", "/social/conversations"),
                ("feed", "/social/feed?limit=20&mode=all"), ("yard", "/app/quintal")):
                response = await client.get(path, headers=headers)
                assert response.status_code == 200, (name, response.status_code)
                body = response.json()
                if name == "pull":
                    assert {row["client_id"] for row in body["changes"]} == set(actor["cat_ids"])
                elif name == "inbox":
                    assert [row["id"] for row in body] == [actor["conversation_id"]]
                elif name == "feed":
                    assert len(body) == 20
                    actor["feed_ids"] = [row["id"] for row in body]
                else:
                    assert isinstance(body, dict) and body
                    actor["yard_keys"] = list(body)
    return actors


async def integrity(factory):
    async with factory() as session:
        return {
            "rows": {model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                for model in (User, RemoteCat, SyncChange, Conversation, ConversationMember, ConversationMessage, FeedItem, VisionJob)},
            "wallets": [list(row) for row in (await session.execute(select(HabitatWallet.user_id, HabitatWallet.balance).order_by(HabitatWallet.user_id))).all()],
            "ledger_rows": await session.scalar(select(func.count()).select_from(HabitatTransaction)),
        }


@pytest.mark.asyncio
async def test_LOAD_authorized_long_campaign(own_api, observe_app, caplog):
    for name in ("httpx", "httpcore", "uvicorn.access", "uvicorn.error"):
        caplog.set_level(logging.WARNING, logger=name)
    scenario = os.environ["QUALITY_LONG_SCENARIO"]
    pilot = scenario == "LOAD-PILOT"
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    folder.mkdir(parents=True, exist_ok=True)
    actors = await seed(own_api)
    if pilot:
        # Exercise real credential renewal before trusting a 60-minute run.
        for actor in actors:
            actor["expires_at"] = 0
    before = await integrity(own_api.factory)
    engine = own_api.factory.kw["bind"]
    pool = engine.sync_engine.pool
    state = observe_app

    def sql(conn, cursor, statement, parameters, context, many):
        phase, route = ROUTE.get()
        verb = statement.lstrip().split(" ", 1)[0].upper()
        state["sql"][(phase, route, verb)] += 1
    event.listen(engine.sync_engine, "before_cursor_execute", sql)
    stopped = asyncio.Event()

    async def monitor():
        with (folder / "server-resources.jsonl").open("w", encoding="utf-8") as log:
            due = time.perf_counter()
            while not stopped.is_set():
                now = time.perf_counter()
                log.write(json.dumps({"phase": state["phase"], "monotonic": now,
                    "loop_lag_ms": max(0, now-due)*1000, "pool_checkedout": pool.checkedout(),
                    "active_http": state["active"], **resources()})+"\n")
                log.flush()
                due = time.perf_counter()+2
                try:
                    await asyncio.wait_for(stopped.wait(), 2)
                except TimeoutError:
                    pass
    watcher = asyncio.create_task(monitor())
    phases = []

    async def phase(name, mode, duration, *, concurrency=1, rate=1, warmup=None, warmup_rate=None):
        nonlocal actors
        out = folder / name
        out.mkdir()
        cfg = {"url": own_api.url, "actors": actors, "mode": mode, "output": str(out),
            "duration_seconds": duration, "warmup_seconds": warmup if warmup is not None else 2 if pilot else 30,
            "concurrency": concurrency, "rate": rate, "warmup_rate": warmup_rate or rate}
        config = out / "private-config.json"
        config.write_text(json.dumps(cfg))
        state["phase"] = name + ":warmup"

        def generate():
            with (out / "generator.log").open("w", encoding="utf-8") as log:
                return run_logged([sys.executable, "-X", "utf8", str(Path(__file__).with_name("long_load_child.py")),
                    "--config", str(config)], cwd=os.environ["QUALITY_TARGET_SOURCE"], env=dict(os.environ),
                    log=log, timeout=duration+cfg["warmup_seconds"]+150)
        pending = asyncio.create_task(asyncio.to_thread(generate))
        deadline = time.monotonic()+cfg["warmup_seconds"]+100
        while not (out / "warmed.json").exists():
            assert not pending.done() and time.monotonic() < deadline, f"Generator warmup failed; inspect {out}"
            await asyncio.sleep(0.1)
        state["phase"] = name + ":measured"
        resource_before = resources()
        (out / "measure.json").write_text("{}\n")
        phase_started = time.monotonic()
        last_update = time.monotonic()
        while not pending.done():
            await asyncio.sleep(1)
            if time.monotonic()-last_update >= 30:
                (folder / "progress.json").write_text(json.dumps({"phase": name,
                    "pool_checkedout": pool.checkedout(), "active_http": state["active"],
                    "server_resources": resources(), "elapsed_seconds": time.monotonic()-phase_started,
                    "seconds_remaining_upper_bound": max(0, duration-(time.monotonic()-phase_started))}))
                last_update = time.monotonic()
        result = await pending
        assert result.returncode == 0, f"Generator failed; inspect {out}"
        data = json.loads((out / "result.json").read_text())
        actors = json.loads((out / "private-accounts.json").read_text())
        assert all(row["valid"] for row in data["responses"] if row["status"] == "200"), "Authenticated response integrity changed"
        data.update(phase=name, server_resources_before=resource_before, server_resources_after=resources())
        phases.append(data)
        (folder / "progress.json").write_text(json.dumps({"phase_completed": name,
            "offered": data["offered"], "completed": data["completed"], "errors": data["errors"]}))
        return data

    try:
        if scenario in ("LOAD-01", "LOAD-PILOT"):
            for concurrency in ([1, 5] if pilot else [1, 5, 10, 25, 50]):
                await phase(f"closed-{concurrency}", "closed", 5 if pilot else 180, concurrency=concurrency)
        if scenario in ("LOAD-02", "LOAD-PILOT"):
            for rate in ([1, 5] if pilot else [1, 5, 10, 25]):
                await phase(f"open-{rate}", "open", 5 if pilot else 180, rate=rate)
        if scenario == "LOAD-06":
            base = json.loads(Path(os.environ["QUALITY_SUSTAINABLE_LOAD_RESULT"]).read_text())
            candidates = [row["rate"] for row in base["phases"]
                if not row["errors"] and all(r["status"] == "200" and r["valid"] for r in row["responses"])
                and row["completed_rps_including_drain"] >= row["rate"]*0.95]
            assert candidates, "No measured sustainable rate; do not invent a soak rate"
            rate = max(1, int(max(candidates)*0.8))
            await phase("burst", "open", 30, rate=min(50, rate*2), warmup_rate=rate)
            state["phase"] = "drain-300s"
            async with httpx.AsyncClient(base_url=own_api.url, timeout=10) as diagnostic:
                for _ in range(60):
                    assert (await diagnostic.get("/health")).status_code == 200
                    await asyncio.sleep(5)
            assert pool.checkedout() == 0 and state["active"] == 0
            await phase("soak-3600s", "open", 3600, rate=rate)
        state["phase"] = "rest"
        await asyncio.sleep(2 if pilot else 15)
        after = await integrity(own_api.factory)
        assert before == after, "Read load changed catalog/conversations/feed/balances/ledger"
        assert pool.checkedout() == 0 and state["active"] == 0
        assert own_api.provider_calls == [], "Load must not call inference"
        # Detailed raw latency arrays stay in each phase; keep the top-level index bounded.
        index = [{k: v for k, v in row.items() if k not in ("latencies_ms", "generator_queue_ms")} for row in phases]
        result = {"scenario": scenario, "pilot_only": pilot, "phases": index,
            "before": before, "after": after, "pool_checkedout_after_rest": pool.checkedout(),
            "server_http": [{"phase": key[0], "route": key[1], "status": key[2], "count": n} for key, n in state["http"].items()],
            "server_sql": [{"phase": key[0], "route": key[1], "verb": key[2], "count": n} for key, n in state["sql"].items()],
            "peak_active_http": state["peak_active"], "paid_calls": 0,
            "scope": "Separate generator PID, own migrated PostgreSQL, real TCP/API, generated accounts. Fixed 30s warmups and planned full durations except explicitly labeled pilot. Shared host, no production capacity or causal speedup claims; percentiles are empirical and require separate uncertainty analysis."}
        evidence("long-load", result)
    finally:
        stopped.set()
        await watcher
        event.remove(engine.sync_engine, "before_cursor_execute", sql)
