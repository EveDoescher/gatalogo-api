"""Capture real API wire data, then audit the frozen Flutter consumer contract."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from sqlalchemy import delete
from websockets.asyncio.client import connect

from network_cases import token, read_event, record
from app.persistence_models import Conversation, ConversationMember, Friendship
from subprocesses import run_logged
from critical_report import flutter_tests

pytest_plugins = ["test_quintal_live_api"]


@pytest.mark.asyncio
async def test_DATA08_real_api_event_to_flutter_controller_characterization(live):
    from uuid import uuid4

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
    run = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
    attempt = Path(os.environ["QUALITY_CAMPAIGN_ATTEMPT"])
    out = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "wire-contract"
    out.mkdir(parents=True, exist_ok=False)
    try:
        async with connect(
            live.url.replace("http://", "ws://") + "/ws?token=" + token(live.other),
            proxy=None,
            open_timeout=5,
            close_timeout=2,
        ) as observer:
            sent = await live.a.post(
                f"/social/conversations/{chat}/messages",
                json={"body": "Synthetic real wire contract message"},
            )
            assert sent.status_code == 201
            event = await read_event(observer, "new_message")
            assert event["data"]["message"]["id"] == sent.json()["id"]
        fixture = out / "api-event.json"
        fixture.write_text(json.dumps(event, indent=2) + "\n", encoding="utf-8")
        consumed = out / "flutter-consumed.json"
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        root = Path(manifest["sources"]["B-flutter"]["path"])
        repo = Path(manifest["sources"]["B-flutter"]["repo"])
        for name in ["package_config.json", "package_graph.json"]:
            shutil.copy2(repo / ".dart_tool" / name, root / ".dart_tool" / name)
        tool = attempt / "executors/campaign_chat_wire_contract_test.dart"
        shutil.copy2(tool, root / "test" / tool.name)
        env = dict(
            os.environ,
            QUALITY_CHAT_WIRE_FILE=str(fixture),
            QUALITY_CHAT_CONSUMED_FILE=str(consumed),
        )
        with (out / "flutter.jsonl").open("w", encoding="utf-8") as log:
            result = await asyncio.to_thread(
                run_logged,
                [
                    shutil.which("flutter"),
                    "test",
                    "--no-pub",
                    "--reporter",
                    "json",
                    f"--dart-define=API_BASE_URL={live.url}",
                    "test/" + tool.name,
                ],
                cwd=root,
                env=env,
                log=log,
                timeout=240,
            )
        cases = flutter_tests(out / "flutter.jsonl")
        assert (
            consumed.is_file()
            and len(cases) == 1
            and cases[0]["name"]
            == "DATA08 real API envelope preserves message identity and content"
        )
        actual = json.loads(consumed.read_text(encoding="utf-8"))
        if result.returncode == 1:
            entries = []
            for line in (
                (out / "flutter.jsonl")
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            ):
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass
            assert any(
                e.get("type") == "print"
                and "The following TestFailure was thrown" in e.get("message", "")
                and "Actual: ''" in e.get("message", "")
                and event["data"]["message"]["id"] in e.get("message", "")
                for e in entries
            )
            assert actual["id"] == "" and actual["body"] == ""
            state = "CONTRACT_FAILURE_REPRODUCED"
        else:
            assert result.returncode == 0
            assert (
                actual["id"] == event["data"]["message"]["id"]
                and actual["body"] == event["data"]["message"]["body"]
            )
            state = "PASS"
        record(
            "DATA08-real-wire-consumer",
            {
                "scenario": "DATA-08",
                "classification": state,
                "api_root_data_keys": sorted(event["data"]),
                "nested_message_id_matches_http": True,
                "flutter_consumed_id_empty": not bool(actual["id"]),
                "flutter_consumed_body_empty": not bool(actual["body"]),
                "wire_fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "flutter_source_sha": manifest["sources"]["B-flutter"]["sha"],
                "finding": "API nests message while consumer decodes the outer data map"
                if state != "PASS"
                else None,
                "scope": "actual API TCP event to actual B controller via injected transport; no phone/real Firebase; red Flutter assertion is preserved, parent test characterizes it",
            },
        )
    finally:
        async with live.db() as session:
            await session.execute(delete(Conversation).where(Conversation.id == chat))
            await session.commit()
