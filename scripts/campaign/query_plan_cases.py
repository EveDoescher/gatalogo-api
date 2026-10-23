"""EXPLAIN ANALYZE on the runner's migrated DB, not production and not a load test."""

import os
from pathlib import Path
import sys
import time

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.engine import make_url

SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
assert SOURCE.is_relative_to(
    Path(os.environ["QUALITY_CAMPAIGN_RUN"]).resolve() / "sources"
)
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "tests"))
pytest_plugins = ["test_quintal_live_api"]
from app.persistence_models import RemoteCat, SyncChange, User
from app.routers import sync
from security_cases import evidence, uid, NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [40, 500, 2000])
async def test_DB04_migrated_sync_query_plans_with_growing_catalog_and_history(
    live, count
):
    url = make_url(os.environ["RECOGNITION_TEST_DATABASE_URL"])
    assert (
        url.database == "recognition_test"
        and url.host == "127.0.0.1"
        and url.port != 5432
    )
    own_ids = [uid("plan-cat-" + str(i)) for i in range(count)]
    client_ids = [uid("plan-client-" + str(i)) for i in range(count)]
    async with live.db() as session:
        assert await session.scalar(text("SELECT current_schema()")) == "public"
        await session.execute(
            insert(RemoteCat),
            [
                {
                    "id": own_ids[i],
                    "client_id": client_ids[i],
                    "user_id": live.uid,
                    "payload": {"name": "plan fixture"},
                    "client_updated_at": NOW,
                    "updated_by_device": "plan",
                }
                for i in range(count)
            ],
        )
        await session.execute(
            insert(SyncChange),
            [
                {"cat_id": ident, "user_id": live.uid}
                for ident in own_ids
                for _ in range(10)
            ],
        )
        await session.commit()
        # Only the disposable migrated tables are analyzed; no live application DB.
        for table in ["cats", "sync_changes", "cat_photos", "cat_avatars"]:
            await session.execute(text("ANALYZE " + table))
        await session.commit()
    captured = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, parameters))

    engine = live.db.kw["bind"]
    async with live.db() as session:
        user = await session.get(User, live.uid)
        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        started = time.perf_counter()
        try:
            result = await sync.pull(cursor=0, limit=20, user=user, session=session)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture)
        elapsed = (time.perf_counter() - started) * 1000
        assert result.changes and {r.client_id for r in result.changes}.issubset(
            set(client_ids)
        )
        await session.rollback()
        connection = await session.connection()
        plans = []
        seen = set()
        for sql, parameters in captured:
            if sql in seen:
                continue
            seen.add(sql)
            explained = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters
            )
            plan = explained.scalar_one()
            plans.append({"sql": sql, "plan": plan})
    assert plans
    evidence(
        "DB04-sync-plans-" + str(count),
        {
            "scenario": "DB-04",
            "catalog_rows": count,
            "history_rows": count * 10,
            "response_rows": len(result.changes),
            "sql_statements": len(captured),
            "internal_pull_elapsed_ms_exploratory": elapsed,
            "plans": plans,
            "scope": "single EXPLAIN sample per SQL shape, real migrated disposable schema and refreshed statistics; not definitive latency improvement or all query families",
        },
    )
