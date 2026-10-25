"""Current isolated TCP fanout with delivery observations and persisted inbox controls."""

import asyncio
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest
import pytest_asyncio

pytest.register_assert_rewrite("socket_fanout_cases", "network_cases")
pytest_plugins = ["current_upload_transport_cases"]
import socket_fanout_cases as fanout
from socket_fanout_cases import (  # noqa: F401
    test_LOAD05_real_message_fanout_reconnect_and_delayed_reader,
)
from measurements import resources
from app.services.websocket_manager import ConnectionManager, ws_manager as manager


@pytest_asyncio.fixture(autouse=True)
async def observed_fanout(live, own_api, monkeypatch, count):
    live.other_uid = own_api.ids[1]
    before = resources()
    snapshots, receipts, starts = [], [], {}
    original_post, original_read = live.a.post, fanout.read_event
    persisted = {}

    async def post(path, **kwargs):
        start = time.perf_counter()
        reply = await original_post(path, **kwargs)
        if path.endswith("/messages") and reply.status_code == 201:
            starts[reply.json()["id"]] = start
            snapshots.append(resources())
            if kwargs.get("json", {}).get("body") == "bounded delayed-reader 9":
                history = await live.other.get(path)
                assert history.status_code == 200
                assert {r["id"] for r in history.json()} == set(starts)
                chat = path.split("/")[3]
                recipient = await live.other.get("/vision/notifications?limit=100")
                sender = await live.a.get("/vision/notifications?limit=100")
                assert recipient.status_code == sender.status_code == 200
                selected = [
                    r
                    for r in recipient.json()
                    if r["type"] == "message"
                    and r["payload"].get("conversation_id") == chat
                ]
                assert len(selected) == 13
                assert not [
                    r
                    for r in sender.json()
                    if r["payload"].get("conversation_id") == chat
                ]
                persisted.update(
                    history_messages=13, recipient_inbox=13, sender_inbox=0
                )
        return reply

    async def read(ws, event):
        result = await original_read(ws, event)
        if event == "new_message":
            ident = result["data"]["message"]["id"]
            receipts.append(
                {
                    "elapsed_ms_from_http_start_to_application_read": (
                        time.perf_counter() - starts[ident]
                    )
                    * 1000
                }
            )
        return result

    monkeypatch.setattr(live.a, "post", post)
    monkeypatch.setattr(fanout, "read_event", read)
    # A failed recipient must not prevent delivery to the other active devices.
    component = ConnectionManager()
    actor = uuid4()

    class Socket:
        def __init__(self, broken=False):
            self.broken, self.messages = broken, []

        async def accept(self):
            pass

        async def send_text(self, message):
            if self.broken:
                raise OSError("Controlled closed recipient")
            self.messages.append(message)

    healthy, broken = [Socket() for _ in range(count)], Socket(True)
    for socket in [*healthy, broken]:
        await component.connect(actor, socket)
    await component.send_to_user(actor, "synthetic", {})
    assert all(len(s.messages) == 1 for s in healthy)
    assert len(component._connections[actor]) == count
    for socket in healthy:
        await component.disconnect(actor, socket)
    assert not component.is_online(actor)
    yield
    pool = own_api.factory.kw["bind"].sync_engine.pool
    for _ in range(20):
        if pool.checkedout() == 0:
            break
        await asyncio.sleep(0.05)
    assert pool.checkedout() == 0
    assert not manager.is_online(own_api.ids[1])
    assert len(starts) == 13 and len(receipts) == 13 * count - max(1, count // 2)
    assert persisted == {
        "history_messages": 13,
        "recipient_inbox": 13,
        "sender_inbox": 0,
    }
    target = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / f"LOAD05-observed-{count}.json"
    assert not target.exists()
    target.write_text(
        json.dumps(
            {
                "devices": count,
                "recipient_failure_component": "one OSError recipient removed, all healthy received exactly one event",
                "before": before,
                "observed_after_posts": snapshots,
                "rest": resources(),
                "pool_checked_out_after_close": pool.checkedout(),
                "presence_after_close": False,
                "persisted": persisted,
                "receipts": receipts,
                "scope": "Three bounded 13-message cases; paused reader is not TCP-buffer saturation. Latency includes HTTP processing, queued frames and deliberate deferred application reads; not network RTT or a capacity/P99 estimate. Resources are the own combined API/test Python process, not Android.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
