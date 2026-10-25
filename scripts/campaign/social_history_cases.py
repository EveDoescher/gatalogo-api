"""Small request batches over controlled larger histories, not a load campaign."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert, text

pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from social_volume_cases import measured
from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
    User,
)


@pytest.mark.asyncio
async def test_DB03_empty_own_list_does_not_include_foreign_history(db):
    ident = uid("empty-own-foreign-history")
    async with db.factory() as session:
        session.add(Conversation(id=ident))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=ident, user_id=uid(who))
                for who in ("B", "C")
            ]
        )
        session.add(
            ConversationMessage(
                conversation_id=ident,
                sender_id=uid("B"),
                body="FOREIGN_EMPTY_LIST_CANARY",
            )
        )
        await session.commit()
    response, samples = await measured(db, "/social/conversations")
    assert response.json() == []
    denied = await db.client.get(
        f"/social/conversations/{ident}/messages", headers=db.headers("A")
    )
    assert denied.status_code == 403
    evidence(
        "DB03-empty-own-list",
        {
            "scenario": "DB-03",
            "own_conversations": 0,
            "foreign_conversations": 1,
            "response": [],
            "foreign_history": 403,
            "samples": samples,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 20, 100])
@pytest.mark.parametrize("messages", [0, 100, 1000])
@pytest.mark.parametrize("authors", ["repeated", "distinct"])
async def test_DB03_LAT05_conversation_histories_preserve_unread_last_message_and_account_boundaries(
    db, count, messages, authors
):
    ids = []
    friendships = set()
    async with db.factory() as session:
        for conversation_index in range(count):
            owner = uid("B") if authors == "repeated" else uuid4()
            if authors == "distinct":
                session.add(
                    User(
                        id=owner,
                        email=f"history-{owner.hex}@example.com",
                        username="hist_" + owner.hex[:20],
                        status="active",
                    )
                )
            chat = uuid4()
            ids.append(chat)
            origin = NOW + timedelta(minutes=conversation_index)
            session.add(Conversation(id=chat, created_at=origin))
            await session.flush()
            if owner not in friendships:
                session.add_all(
                    [
                        Friendship(user_id=uid("A"), friend_id=owner),
                        Friendship(user_id=owner, friend_id=uid("A")),
                    ]
                )
                friendships.add(owner)
            session.add_all(
                [
                    ConversationMember(conversation_id=chat, user_id=user)
                    for user in [uid("A"), owner]
                ]
            )
            await session.flush()
            if messages:
                # One bounded 1,000-row batch maximum; peak fixture is 100,000 rows.
                await session.execute(
                    insert(ConversationMessage),
                    [
                        {
                            "id": uuid4(),
                            "conversation_id": chat,
                            "sender_id": owner if index % 2 == 0 else uid("A"),
                            "body": f"synthetic history {conversation_index}:{index}",
                            "created_at": origin + timedelta(milliseconds=index),
                            "read_at": origin + timedelta(seconds=2)
                            if index <= messages // 2
                            else None,
                            "type": "text",
                        }
                        for index in range(messages)
                    ],
                )
        foreign = uuid4()
        session.add(Conversation(id=foreign, created_at=NOW))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=foreign, user_id=user)
                for user in [uid("B"), uid("C")]
            ]
        )
        session.add(
            ConversationMessage(
                conversation_id=foreign,
                sender_id=uid("C"),
                body="FOREIGN_HISTORY_CANARY",
                created_at=NOW,
            )
        )
        await session.commit()
        await session.execute(text("ANALYZE conversation_messages"))
        await session.commit()
    expected_unread = sum(
        index % 2 == 0 and index > messages // 2 for index in range(messages)
    )
    response, samples = await measured(db, "/social/conversations")
    rows = response.json()
    assert {row["id"] for row in rows} == {str(chat) for chat in ids}
    assert (
        "FOREIGN_HISTORY_CANARY" not in response.text
        and str(foreign) not in response.text
    )
    assert all(row["unread_count"] == expected_unread for row in rows)
    for row in rows:
        if messages:
            index = ids.index(next(chat for chat in ids if str(chat) == row["id"]))
            assert (
                row["last_message"]["body"]
                == f"synthetic history {index}:{messages - 1}"
            )
        else:
            assert row["last_message"] is None
    history, history_samples = await measured(
        db, f"/social/conversations/{ids[0]}/messages", {"limit": 20}
    )
    records = history.json()
    assert len(records) == messages
    assert len({row["id"] for row in records}) == messages
    assert [row["body"] for row in records] == [
        f"synthetic history 0:{index}" for index in range(messages)
    ]
    denied = await db.client.get(
        f"/social/conversations/{foreign}/messages", headers=db.headers("A")
    )
    assert denied.status_code == 403
    evidence(
        f"DB03-history-{count}-{messages}-{authors}",
        {
            "scenario": "DB-03",
            "conversations": count,
            "messages_per_conversation": messages,
            "fixture_message_rows": count * messages + 1,
            "authors": authors,
            "unread_per_conversation": expected_unread,
            "list_samples": samples,
            "history_samples": history_samples,
            "requested_limit_probe": 20,
            "history_rows_returned": len(records),
            "foreign_history_status": denied.status_code,
            "current_contract": "Messages route returns full ordered history; it declares no pagination parameter. An extra limit query does not paginate it.",
            "scope": "real PG with ORM metadata schema and refreshed table statistics, ASGI HTTP/auth,3 requests per path (first warmup); not migrated-deployment certification, sustained capacity, phone latency or reliable tail/gain estimate",
        },
    )
