"""Legacy-equivalent pagination across planned histories and a bounded larger history."""

import time

import pytest
from sqlalchemy import event, func, insert, select, text

pytest.register_assert_rewrite("security_cases", "test_sync_query_performance")
pytest_plugins = ["security_cases"]
from security_cases import evidence
from test_sync_query_performance import legacy_pull, seed
from app.persistence_models import SyncChange
from app.routers.sync import pull


@pytest.mark.asyncio
@pytest.mark.parametrize("revisions", [1, 10, 100, 1000])
async def test_DB02_history_full_json_cursor_sql_and_repeat(db, revisions):
    count = 20 if revisions == 1000 else 5
    user, cats = await seed(db.factory, count)
    async with db.factory() as session:
        additional = [
            dict(user_id=user.id, cat_id=cat.id)
            for cat in cats
            for _ in range(revisions - 1)
        ]
        if additional:
            await session.execute(insert(SyncChange), additional)
        await session.commit()
        all_revisions = list(
            await session.scalars(
                select(SyncChange.revision)
                .where(SyncChange.user_id == user.id)
                .order_by(SyncChange.revision)
            )
        )
    assert len(all_revisions) == count * revisions + 1
    samples = []

    async def page(cursor, limit):
        async with db.factory() as session:
            expected = await legacy_pull(session, user, cursor, limit)
        # A new connection clears its prepared-statement cache, not PG/OS buffers.
        await db.engine.dispose()
        for repetition in (False, True):
            statements = []

            def observe(conn, cur, statement, parameters, context, many):
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)

            async with db.factory() as session:
                event.listen(db.engine.sync_engine, "before_cursor_execute", observe)
                started = time.perf_counter()
                try:
                    actual = await pull(
                        cursor=cursor, limit=limit, user=user, session=session
                    )
                finally:
                    elapsed = time.perf_counter() - started
                    event.remove(
                        db.engine.sync_engine, "before_cursor_execute", observe
                    )
            assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
            assert len(statements) == (4 if expected.changes else 1)
            samples.append(
                dict(
                    cursor=cursor,
                    limit=limit,
                    repeated=repetition,
                    next_cursor=actual.next_cursor,
                    records=len(actual.changes),
                    selects_excluding_auth=len(statements),
                    service_ms=elapsed * 1000,
                )
            )
        return expected

    for limit in (1, 200, 500):
        if revisions == 1000 and limit == 1:
            # Original F-HIST combinations traverse fully below. Extra 20k case
            # checks boundary pages at limit=1, avoiding 40k repeated requests.
            for cursor in (
                0,
                all_revisions[len(all_revisions) // 2] - 1,
                all_revisions[-2],
                all_revisions[-1],
            ):
                await page(cursor, limit)
            continue
        cursor, seen = 0, set()
        while True:
            result = await page(cursor, limit)
            if not result.changes:
                break
            assert result.next_cursor > cursor
            seen.update(record.client_id for record in result.changes)
            cursor = result.next_cursor
        assert cursor == all_revisions[-1]
        assert seen == {cat.client_id for cat in cats}

    async with db.factory() as session:
        await session.execute(text("ANALYZE sync_changes"))
        query = (
            select(SyncChange.cat_id, func.max(SyncChange.revision))
            .where(SyncChange.cat_id.in_([cat.id for cat in cats]))
            .group_by(SyncChange.cat_id)
        )
        compiled = str(
            query.compile(
                dialect=db.engine.dialect, compile_kwargs={"literal_binds": True}
            )
        )
        plan = await session.scalar(
            text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + compiled)
        )
    evidence(
        "DB02-complete-history-" + str(revisions),
        dict(
            revisions_per_cat=revisions,
            cats=count,
            own_revisions=len(all_revisions),
            full_traversal_limits=[200, 500] if revisions == 1000 else [1, 200, 500],
            sampled_limit_one_boundary_pages=revisions == 1000,
            json_and_cursor_equal_to_legacy=True,
            samples=samples,
            max_revision_query_plan=plan,
            timing="Service-only diagnostic; excludes oracle; first call uses fresh connection, repeat reuses pool. Database/OS buffers warmed by fixtures/oracle; not physical cold IO or confirmatory latency.",
        ),
    )
