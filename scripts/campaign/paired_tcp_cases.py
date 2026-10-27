"""Common external TCP observer for exploratory A/B blocks; no device or real IA."""

from collections import Counter
from contextvars import ContextVar
import itertools
import time
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import event, func, select, text

pytest_plugins = ["current_offline_cases"]
from app import main
from app.persistence_models import CatPhoto, RemoteCat, SyncChange, VisionJob
import latency_pressure_cases as pressure
from security_cases import evidence
from measurements import hardware

PATH = ContextVar("quality_paired_request_path", default="fixture")


@pytest.fixture(autouse=True)
def observed(monkeypatch):
    original, http = main.app, Counter()
    class Observer:
        def __getattr__(self, name):
            return getattr(original, name)
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await original(scope, receive, send)
            token = PATH.set(scope["path"])
            async def record(message):
                if message["type"] == "http.response.start":
                    http[(scope["path"], message["status"])] += 1
                await send(message)
            try:
                await original(scope, receive, record)
            finally:
                PATH.reset(token)
    monkeypatch.setattr(main, "app", Observer())
    return http


@pytest.mark.asyncio
async def test_BR02_common_tcp_workload(own_api, observed, monkeypatch, caplog):
    sequence = itertools.count()
    monkeypatch.setattr(pressure, "uuid4", lambda: uuid5(NAMESPACE_URL, f"gatalogo-paired-tcp-{next(sequence)}"))
    sql = Counter()
    engine = own_api.factory.kw["bind"]
    def query(conn, cursor, statement, parameters, context, many):
        sql[(PATH.get(), statement.lstrip().split(" ", 1)[0].upper())] += 1
    event.listen(engine.sync_engine, "before_cursor_execute", query)
    started = time.perf_counter()
    try:
        await pressure.test_LAT08_tcp_fixed_load_during_controlled_inference(own_api, monkeypatch, caplog)
        async with own_api.factory() as session:
            counts = {model.__name__: await session.scalar(select(func.count()).select_from(model))
                for model in (CatPhoto, RemoteCat, SyncChange, VisionJob)}
            database = await session.scalar(text("SELECT version()"))
        assert engine.sync_engine.pool.checkedout() == 0
        assert all(status == 200 for (_, status) in observed)
        evidence("BR02-tcp", {"scenario": "BR-02", "elapsed_seconds": time.perf_counter()-started,
            "http": [{"route": key[0], "status": key[1], "count": count} for key, count in sorted(observed.items())],
            "sql": [{"route": key[0], "verb": key[1], "count": count} for key, count in sorted(sql.items())],
            "final_counts": counts, "database_version": database,
            "hardware": hardware(),
            "fixture_id_strategy": "UUIDv5 deterministic counter reset per block; same catalog/history data and controlled analysis, fresh disposable credentials.",
            "scope": "Three routes x configured fixed sample count x 0/1/5 I/O-gated analyses; C5 and fixed warmups. Separate generator, actual TCP, pool/lag/CPU/RSS/SQL observations. New internal reference worker is absent on main and is not imported by this common workload. Shared host; background conditions recorded by campaign. No causal gain, population P99 or complete mobile/full BR-02 certification."})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", query)
