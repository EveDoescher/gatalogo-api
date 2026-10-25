"""Explain captured real SELECTs against this runner's migrated disposable DB."""

from copy import deepcopy
from datetime import timedelta
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, NOW
from app.persistence_models import (
    CatAvatar,
    CatPhoto,
    Conversation,
    ConversationMember,
    ConversationMessage,
    FeedItem,
    ProductState,
    RemoteCat,
    SyncChange,
    User,
)
from app.routers import sync
from app.services import chat_queries, social_feed_queries


@pytest_asyncio.fixture
async def migrated():
    url = os.environ["RECOGNITION_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert (
        parsed.host == "127.0.0.1"
        and parsed.port != 5432
        and parsed.database == "recognition_test"
    )
    assert url == os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    users, chats = [uuid4(), uuid4()], []
    namespace = uuid4().hex
    try:
        async with factory() as session:
            assert await session.scalar(text("SELECT current_schema()")) == "public"
            assert await session.scalar(text("SELECT version_num FROM alembic_version"))
            session.add_all(
                [
                    User(
                        id=ident,
                        email=f"plan-{namespace}-{i}@example.invalid",
                        username=f"plan_{ident.hex[:20]}",
                        status="active",
                    )
                    for i, ident in enumerate(users)
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ProductState(
                        user_id=ident,
                        preferences={"gallery_audience": "public"},
                        achievements={},
                    )
                    for ident in users
                ]
            )
            await session.commit()
        yield SimpleNamespace(
            engine=engine,
            factory=factory,
            users=users,
            chats=chats,
            namespace=namespace,
        )
    finally:
        async with factory() as session:
            if chats:
                await session.execute(
                    delete(Conversation).where(Conversation.id.in_(chats))
                )
            await session.execute(
                delete(User).where(
                    User.id.in_(users),
                    User.email.like(f"plan-{namespace}-%@example.invalid"),
                )
            )
            await session.commit()
        await engine.dispose()


async def capture_plans(fixture, family, call):
    statements = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, deepcopy(parameters)))

    async with fixture.factory() as session:
        user = await session.get(User, fixture.users[0])
        event.listen(fixture.engine.sync_engine, "before_cursor_execute", capture)
        started = time.perf_counter()
        try:
            result = await call(session, user)
        finally:
            event.remove(fixture.engine.sync_engine, "before_cursor_execute", capture)
        elapsed = (time.perf_counter() - started) * 1000
        count = len(result.changes) if family == "sync" else len(result)
        await session.rollback()
        await session.execute(text("SET TRANSACTION READ ONLY"))
        connection = await session.connection()
        plans, seen = [], set()
        for sql, parameters in statements:
            if sql in seen:
                continue
            seen.add(sql)
            explained = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters
            )
            plans.append({"sql": sql, "plan": explained.scalar_one()})
        assert plans and all(p["plan"][0]["Plan"]["Node Type"] for p in plans)
    return {
        "family": family,
        "response_rows": count,
        "service_selects": len(statements),
        "distinct_sql_shapes": len(plans),
        "service_elapsed_ms_observed": elapsed,
        "plans": plans,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("cats,chats", [(40, 1), (500, 20), (2000, 100)])
async def test_DB04_migrated_catalog_conversation_and_feed_select_plans(
    migrated, cats, chats
):
    owner, other = migrated.users
    cat_ids = [uuid4() for _ in range(cats)]
    async with migrated.factory() as session:
        await session.execute(
            insert(RemoteCat),
            [
                {
                    "id": ident,
                    "client_id": uuid4(),
                    "user_id": owner,
                    "payload": {"name": "synthetic"},
                    "client_updated_at": NOW,
                    "updated_by_device": "plans",
                    "photo_key": f"plans/{ident}.png",
                    "photo_hash": "a" * 64,
                }
                for ident in cat_ids
            ],
        )
        await session.execute(
            insert(SyncChange),
            [
                {"cat_id": ident, "user_id": owner}
                for ident in cat_ids
                for _ in range(10)
            ],
        )
        await session.execute(
            insert(CatPhoto),
            [
                {
                    "id": uuid4(),
                    "cat_id": ident,
                    "kind": kind,
                    "storage_key": f"plans/{ident}-{kind}.png",
                    "content_hash": "a" * 64,
                    "content_type": "image/png",
                    "status": "ready",
                }
                for ident in cat_ids
                for kind in ("primary", "front", "left", "right", "back")
            ],
        )
        await session.execute(
            insert(CatAvatar),
            [
                {
                    "cat_id": ident,
                    "status": "ready",
                    "texture_keys": {},
                    "preview_key": f"plans/{ident}.png",
                }
                for ident in cat_ids[::4]
            ],
        )
        await session.execute(
            insert(FeedItem),
            [
                {
                    "id": uuid4(),
                    "user_id": other,
                    "type": "cat_post",
                    "payload": {"is_public": True},
                    "created_at": NOW + timedelta(milliseconds=i),
                }
                for i in range(cats)
            ],
        )
        migrated.chats.extend(uuid4() for _ in range(chats))
        await session.execute(
            insert(Conversation),
            [
                {"id": ident, "created_at": NOW + timedelta(seconds=i)}
                for i, ident in enumerate(migrated.chats)
            ],
        )
        await session.execute(
            insert(ConversationMember),
            [
                {"conversation_id": ident, "user_id": user}
                for ident in migrated.chats
                for user in (owner, other)
            ],
        )
        await session.execute(
            insert(ConversationMessage),
            [
                {
                    "id": uuid4(),
                    "conversation_id": ident,
                    "sender_id": other,
                    "body": "synthetic plan history",
                    "type": "text",
                    "created_at": NOW + timedelta(milliseconds=i),
                }
                for ident in migrated.chats
                for i in range(20)
            ],
        )
        await session.commit()
        for table in (
            "cats",
            "sync_changes",
            "cat_photos",
            "cat_avatars",
            "users",
            "product_states",
            "feed_items",
            "conversations",
            "conversation_members",
            "conversation_messages",
        ):
            await session.execute(text("ANALYZE " + table))
        await session.commit()
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        indexes = (
            (
                await session.execute(
                    text(
                        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname"
                    )
                )
            )
            .mappings()
            .all()
        )
    sync_plan = await capture_plans(
        migrated,
        "sync",
        lambda session, user: sync.pull(
            cursor=0, limit=200, user=user, session=session
        ),
    )
    chat_plan = await capture_plans(
        migrated,
        "conversations",
        lambda session, user: chat_queries.conversations_list(
            user=user, session=session
        ),
    )
    feed_plan = await capture_plans(
        migrated,
        "feed",
        lambda session, user: social_feed_queries.feed(
            limit=20,
            cursor=None,
            cursor_id=None,
            mode="all",
            user=user,
            session=session,
        ),
    )
    assert 0 < sync_plan["response_rows"] <= 200
    assert chat_plan["response_rows"] == chats
    assert feed_plan["response_rows"] == 20
    evidence(
        f"DB04-migrated-plans-{cats}",
        {
            "scenario": "DB-04",
            "schema": "public migrated by runner alembic upgrade head",
            "migration_version": version,
            "rows": {
                "cats": cats,
                "revisions": cats * 10,
                "photos": cats * 5,
                "conversations": chats,
                "messages": chats * 20,
                "feed": cats,
            },
            "statistics_refreshed": True,
            "select_explain_transactions_read_only": True,
            "families": [sync_plan, chat_plan, feed_plan],
            "indexes": [dict(row) for row in indexes],
            "scope": "Three critical read families, actual captured service SELECTs; parameters stay private, no commands explained. Warm fixture and single sample per shape; no cold IO, auth overhead, P99 or gain inference.",
        },
    )
