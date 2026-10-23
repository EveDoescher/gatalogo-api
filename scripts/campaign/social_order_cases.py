"""Probe deterministic presentation for identical logical rows with tied timestamps."""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select

pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
)


async def logical_rows(factory, chat):
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == chat)
                    .order_by(ConversationMessage.id)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                column.name: getattr(row, column.name)
                for column in ConversationMessage.__table__.columns
            }
            for row in rows
        ]


@pytest.mark.asyncio
async def test_DATA08_DB03_identical_message_rows_with_equal_times_keep_presentation_order(
    db,
):
    chat = uuid4()
    rows = [
        {
            "id": uuid4(),
            "conversation_id": chat,
            "sender_id": uid("B"),
            "body": body,
            "type": "text",
            "created_at": NOW,
        }
        for body in ["synthetic first", "synthetic second"]
    ]
    async with db.factory() as session:
        session.add(Conversation(id=chat, created_at=NOW))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=user)
                for user in [uid("A"), uid("B")]
            ]
        )
        session.add_all(
            [
                Friendship(user_id=uid("A"), friend_id=uid("B")),
                Friendship(user_id=uid("B"), friend_id=uid("A")),
            ]
        )
        await session.flush()
        await session.execute(insert(ConversationMessage), rows)
        await session.commit()
    before = await logical_rows(db.factory, chat)

    async def view():
        history = await db.client.get(
            f"/social/conversations/{chat}/messages", headers=db.headers("A")
        )
        inbox = await db.client.get("/social/conversations", headers=db.headers("A"))
        assert history.status_code == inbox.status_code == 200
        summary = next(row for row in inbox.json() if row["id"] == str(chat))
        return {
            "history_ids": [row["id"] for row in history.json()],
            "last_message_id": summary["last_message"]["id"],
            "unread": summary["unread_count"],
        }

    first = await view()
    async with db.factory() as session:
        await session.execute(
            delete(ConversationMessage).where(
                ConversationMessage.conversation_id == chat
            )
        )
        await session.execute(insert(ConversationMessage), list(reversed(rows)))
        await session.commit()
    after = await logical_rows(db.factory, chat)
    assert before == after, (
        "Logical fixture rows must remain byte-equivalent after canonical serialization"
    )
    second = await view()
    assert first["unread"] == second["unread"] == 2
    evidence(
        "DATA08-chat-equal-time-physical-order",
        {
            "scenario": "DATA-08",
            "related": ["DB-03"],
            "logical_rows_equal": True,
            "logical_rows_sha256": hashlib.sha256(
                json.dumps(before, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "first": first,
            "after_reinsertion": second,
            "history_order_invariant": first["history_ids"] == second["history_ids"],
            "last_message_invariant": first["last_message_id"]
            == second["last_message_id"],
            "scope": "two own messages with equal times and identical logical rows; only physical insertion order changes. Does not prescribe which tie-break key should be chosen or alter user-visible ordering.",
        },
    )
    assert first == second, (
        "Presentation of tied timestamps depends on physical row order"
    )
