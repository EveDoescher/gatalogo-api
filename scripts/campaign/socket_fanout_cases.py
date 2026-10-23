"""Short real-TCP fanout/reconnect and delayed-reader checks, not sustained load."""

import asyncio
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from websockets.asyncio.client import connect

from network_cases import token, read_event, record
from app.persistence_models import (
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
)

pytest_plugins = ["test_quintal_live_api"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 10, 50])
async def test_LOAD05_real_message_fanout_reconnect_and_delayed_reader(live, count):
    chat = uuid4()
    async with live.db() as session:
        session.add(Conversation(id=chat))
        await session.flush()
        session.add_all(
            [
                ConversationMember(conversation_id=chat, user_id=user)
                for user in [live.uid, live.other_uid]
            ]
        )
        session.add_all(
            [
                Friendship(user_id=a, friend_id=b)
                for a, b in [(live.uid, live.other_uid), (live.other_uid, live.uid)]
            ]
        )
        await session.commit()
    base = live.url.replace("http://", "ws://") + "/ws?token="

    async def send(label):
        response = await live.a.post(
            f"/social/conversations/{chat}/messages", json={"body": label}
        )
        assert response.status_code == 201
        return response.json()["id"]

    try:
        async with AsyncExitStack() as stack:
            sockets = [
                await stack.enter_async_context(
                    connect(
                        base + token(live.other),
                        proxy=None,
                        open_timeout=5,
                        close_timeout=2,
                    )
                )
                for _ in range(count)
            ]
            online = await live.a.get(f"/social/conversations/{chat}")
            assert online.status_code == 200 and online.json()["is_online"]
            first = await send("bounded first message")
            received = await asyncio.gather(
                *[read_event(ws, "new_message") for ws in sockets]
            )
            assert all(r["data"]["message"]["id"] == first for r in received)
            closed = sockets[: max(1, count // 2)]
            remaining = sockets[len(closed) :]
            for ws in closed:
                await ws.close()
                await ws.wait_closed()
            second = await send("bounded disconnected message")
            received = await asyncio.gather(
                *[read_event(ws, "new_message") for ws in remaining]
            )
            assert all(r["data"]["message"]["id"] == second for r in received)
            replacements = [
                await stack.enter_async_context(
                    connect(
                        base + token(live.other),
                        proxy=None,
                        open_timeout=5,
                        close_timeout=2,
                    )
                )
                for _ in closed
            ]
            active = remaining + replacements
            third = await send("bounded reconnect message")
            received = await asyncio.gather(
                *[read_event(ws, "new_message") for ws in active]
            )
            assert len(received) == count and all(
                r["data"]["message"]["id"] == third for r in received
            )
            slow = active[0]
            slow.transport.pause_reading()
            try:
                ids = [
                    await send("bounded delayed-reader " + str(i)) for i in range(10)
                ]

                async def consume(ws):
                    return [
                        (await read_event(ws, "new_message"))["data"]["message"]["id"] for _ in ids
                    ]

                fast = await asyncio.gather(*[consume(ws) for ws in active[1:]])
                assert all(row == ids for row in fast)
            finally:
                slow.transport.resume_reading()
            delayed = await consume(slow)
            assert delayed == ids
        for _ in range(20):
            offline = await live.a.get(f"/social/conversations/{chat}")
            if not offline.json()["is_online"]:
                break
            await asyncio.sleep(0.05)
        assert offline.status_code == 200 and not offline.json()["is_online"]
        async with live.db() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ConversationMessage)
                    .where(ConversationMessage.conversation_id == chat)
                )
                == 13
            )
        record(
            "LOAD05-tcp-" + str(count),
            {
                "scenario": "LOAD-05",
                "devices": count,
                "partial_reconnect_devices": len(closed),
                "real_committed_messages": 13,
                "message_fanout_correct": True,
                "delayed_reader_drained_messages": 10,
                "fast_reader_count": count - 1,
                "server_presence_after_all_closed": False,
                "scope": "bounded functional TCP burst with one paused transport; not backpressure saturation, capacity, battery or prolonged load evidence",
            },
        )
    finally:
        async with live.db() as session:
            await session.execute(delete(Conversation).where(Conversation.id == chat))
            await session.commit()
