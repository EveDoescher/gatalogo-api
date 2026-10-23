"""Bounded SQL-volume and privacy/pagination probes, real disposable PostgreSQL."""

from datetime import timedelta
import time
from uuid import uuid4

import pytest
from sqlalchemy import event

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    FeedItem,
    Friendship,
    ProductState,
    User,
)
from measurements import resources


async def measured(db, path, params=None):
    reads = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
    samples = []
    try:
        for index in range(3):
            reads.clear()
            before = resources()
            start = time.perf_counter()
            response = await db.client.get(path, headers=db.headers("A"), params=params)
            elapsed = (time.perf_counter() - start) * 1000
            after = resources()
            assert response.status_code == 200
            samples.append(
                {
                    "warmup": index == 0,
                    "sql_selects": len(reads),
                    "elapsed_ms_exploratory": elapsed,
                    "response_bytes": len(response.content),
                    "cpu_seconds_delta": after["process_cpu_seconds"]
                    - before["process_cpu_seconds"],
                    "rss_bytes": after.get("rss_bytes"),
                }
            )
        return response, samples
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", capture)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 20, 100])
@pytest.mark.parametrize("authors", ["repeated", "distinct"])
async def test_DB03_LAT05_conversation_volume_sql_and_isolation(db, count, authors):
    ids = []
    async with db.factory() as session:
        for i in range(count):
            owner = uid("B") if authors == "repeated" else uuid4()
            if authors == "distinct":
                session.add(
                    User(
                        id=owner,
                        email=f"volume-{owner.hex}@example.com",
                        username="volume_" + owner.hex[:20],
                        status="active",
                    )
                )
            chat = uuid4()
            ids.append(str(chat))
            session.add(Conversation(id=chat, created_at=NOW + timedelta(seconds=i)))
            await session.flush()
            session.add_all(
                [
                    ConversationMember(conversation_id=chat, user_id=u)
                    for u in [uid("A"), owner]
                ]
            )
            session.add(
                ConversationMessage(
                    conversation_id=chat,
                    sender_id=owner,
                    body="synthetic volume",
                    created_at=NOW + timedelta(seconds=i),
                )
            )
        foreign = uuid4()
        session.add(Conversation(id=foreign))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=foreign, user_id=u)
                for u in [uid("B"), uid("C")]
            ]
        )
        await session.commit()
    response, samples = await measured(db, "/social/conversations")
    rows = response.json()
    assert {x["id"] for x in rows} == set(ids) and str(foreign) not in {
        x["id"] for x in rows
    }
    assert all(x["unread_count"] == 1 for x in rows)
    evidence(
        f"DB03-conversations-{authors}-{count}",
        {
            "scenario": "DB-03",
            "conversations": count,
            "authors": authors,
            "samples": samples,
            "integrity": "all own, no foreign, unread preserved",
            "interpretation": "SQL structural probe; three requests do not estimate reliable tails or latency gains",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 20, 100])
@pytest.mark.parametrize("authors", ["repeated", "distinct"])
async def test_DB03_LAT07_feed_volume_equal_timestamps_and_pagination(
    db, count, authors
):
    expected = []
    async with db.factory() as session:
        b = await session.get(User, uid("B"))
        b.username = "volume_b"
        session.add(
            ProductState(user_id=uid("B"), preferences={"gallery_audience": "public"})
        )
        for i in range(count):
            owner = uid("B") if authors == "repeated" else uuid4()
            if authors == "distinct":
                session.add(
                    User(
                        id=owner,
                        email=f"feed-{owner.hex}@example.com",
                        username="feed_" + owner.hex[:20],
                        status="active",
                    )
                )
                await session.flush()
                session.add(
                    ProductState(
                        user_id=owner, preferences={"gallery_audience": "public"}
                    )
                )
            ident = uuid4()
            expected.append(str(ident))
            session.add(
                FeedItem(
                    id=ident,
                    user_id=owner,
                    type="cat_post",
                    payload={"is_public": True},
                    created_at=NOW,
                )
            )
        await session.commit()
    response, samples = await measured(
        db, "/social/feed", {"limit": min(count, 100), "mode": "all"}
    )
    assert {x["id"] for x in response.json()} == set(expected)
    cursor = response.json()[-1]
    end = await db.client.get(
        "/social/feed",
        headers=db.headers("A"),
        params={
            "limit": 100,
            "cursor": cursor["created_at"],
            "cursor_id": cursor["id"],
        },
    )
    assert end.status_code == 200 and end.json() == []
    # Page through the same timestamp one item at a time to check the ID tiebreak.
    if count == 20:
        seen = []
        params = {"limit": 1}
        for _ in range(count + 1):
            page = await db.client.get(
                "/social/feed", headers=db.headers("A"), params=params
            )
            assert page.status_code == 200
            if not page.json():
                break
            row = page.json()[0]
            seen.append(row["id"])
            params = {"limit": 1, "cursor": row["created_at"], "cursor_id": row["id"]}
        assert (
            len(seen) == count
            and len(set(seen)) == count
            and set(seen) == set(expected)
        )
    evidence(
        f"DB03-feed-{authors}-{count}",
        {
            "scenario": "DB-03",
            "posts": count,
            "authors": authors,
            "samples": samples,
            "equal_timestamp_tiebreak": True,
            "no_omission_or_duplicates": True,
            "interpretation": "SQL structural probe; three requests do not estimate reliable tails",
        },
    )


@pytest.mark.asyncio
async def test_SEC04_LAT07_privacy_changes_between_feed_pages(db):
    async with db.factory() as session:
        b = await session.get(User, uid("B"))
        b.username = "private_b"
        session.add_all(
            [
                Friendship(user_id=uid("A"), friend_id=uid("B")),
                Friendship(user_id=uid("B"), friend_id=uid("A")),
            ]
        )
        # More private rows than one scan chunk, followed by public rows.
        for i in range(35):
            session.add(
                FeedItem(
                    user_id=uid("B"),
                    type="cat_post",
                    payload={"is_public": False, "canary": "private-page"},
                    created_at=NOW - timedelta(seconds=i),
                )
            )
        for i in range(8):
            session.add(
                FeedItem(
                    user_id=uid("B"),
                    type="cat_post",
                    payload={"is_public": True},
                    created_at=NOW - timedelta(seconds=100 + i),
                )
            )
        await session.commit()
    first = await db.client.get(
        "/social/feed", headers=db.headers("A"), params={"limit": 3}
    )
    assert first.status_code == 200 and len(first.json()) == 3
    last = first.json()[-1]
    # Revoke friendship after a cursor is obtained. Old cursor cannot retain access.
    from sqlalchemy import delete

    async with db.factory() as session:
        await session.execute(
            delete(Friendship).where(
                Friendship.user_id == uid("A"), Friendship.friend_id == uid("B")
            )
        )
        await session.commit()
    following = await db.client.get(
        "/social/feed",
        headers=db.headers("A"),
        params={"limit": 5, "cursor": last["created_at"], "cursor_id": last["id"]},
    )
    assert following.status_code == 200 and len(following.json()) == 5
    assert "private-page" not in following.text
    assert not {x["id"] for x in first.json()} & {x["id"] for x in following.json()}
    stranger = await db.client.get(
        "/social/feed", headers=db.headers("C"), params={"limit": 5}
    )
    assert stranger.status_code == 200 and "private-page" not in stranger.text
    evidence(
        "SEC04-feed-revocation-pages",
        {
            "scenario": "SEC-04",
            "initial_friend_rows": 3,
            "after_revocation_public_rows": 5,
            "private_scan_rows": 35,
            "foreign_canary_absent": True,
            "scope": "post privacy and friendship change; gallery preference and report lifecycle tested separately",
        },
    )
