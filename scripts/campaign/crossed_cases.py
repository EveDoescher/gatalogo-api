"""Cross fixed Flutter/API revisions through real HTTP and a common test script."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse

import httpx
import pytest

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
ATTEMPT = Path(os.environ["QUALITY_CAMPAIGN_ATTEMPT"])
SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
assert SOURCE.is_relative_to(RUN / "sources")
sys.path.insert(0, str(SOURCE / "tests"))
pytest_plugins = ["test_quintal_live_api"]
from subprocesses import run_logged


@pytest.mark.asyncio
@pytest.mark.parametrize("client_arm", ["A", "B"])
async def test_BR04_crossed_client_api_commit_loss_restart_and_account(
    live, client_arm
):
    assert (
        urlparse(live.url).hostname == "127.0.0.1" and urlparse(live.url).port != 8000
    )
    root = RUN / "sources" / client_arm / "flutter"
    output = ATTEMPT / ("client-" + client_arm)
    output.mkdir(exist_ok=False)
    (root / ".dart_tool").mkdir(exist_ok=True)
    manifest = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    for name in ["package_config.json", "package_graph.json"]:
        shutil.copy2(repo / ".dart_tool" / name, root / ".dart_tool" / name)
    test = ATTEMPT / "executors/campaign_crossed_client_test.dart"
    target = root / "test/campaign_crossed_client_test.dart"
    # The same external executor is injected into each frozen package; lib stays intact.
    shutil.copy2(test, target)
    storage = output / "sqlite"
    storage.mkdir()
    operations, errors = [], []
    dropped = False
    async with httpx.AsyncClient(base_url=live.url, timeout=20) as upstream:

        async def proxy(reader, writer):
            nonlocal dropped
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 20)
                lines = head.decode("latin-1").split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                headers = {
                    k.lower(): v.strip()
                    for k, v in (
                        line.split(":", 1) for line in lines[1:] if ":" in line
                    )
                }
                body = await reader.readexactly(int(headers.get("content-length", "0")))
                response = await upstream.request(
                    method,
                    path,
                    headers={
                        k: v
                        for k, v in headers.items()
                        if k
                        not in {
                            "host",
                            "connection",
                            "content-length",
                            "accept-encoding",
                        }
                    },
                    content=body,
                )
                if path == "/app/quintal/operations":
                    command = json.loads(body)
                    operations.append((command["action"], command["idempotency_key"]))
                    if not dropped:
                        assert response.status_code == 200
                        dropped = True
                        return
                payload = response.content
                writer.write(
                    (
                        f"HTTP/1.1 {response.status_code} OK\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + payload
                )
                await writer.drain()
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            for phase, client in [("before", live.a), ("after", live.b)]:
                options = {
                    "phase": phase,
                    "database": str(storage),
                    "account": {
                        "id": str(live.uid),
                        "token": client.headers["authorization"].removeprefix(
                            "Bearer "
                        ),
                    },
                    "other": {
                        "id": str(live.other_uid),
                        "token": live.other.headers["authorization"].removeprefix(
                            "Bearer "
                        ),
                    },
                }
                env = dict(os.environ, QUALITY_CROSSED_CLIENT=json.dumps(options))
                with (output / (phase + ".jsonl")).open("w", encoding="utf-8") as log:
                    result = await asyncio.to_thread(
                        run_logged,
                        [
                            shutil.which("flutter"),
                            "test",
                            "--no-pub",
                            "--reporter",
                            "json",
                            f"--dart-define=API_BASE_URL=http://127.0.0.1:{port}",
                            "test/campaign_crossed_client_test.dart",
                        ],
                        cwd=root,
                        env=env,
                        log=log,
                        timeout=240,
                    )
                assert result.returncode == 0, "Inspect private phase JSONL"
            assert (
                dropped
                and not errors
                and len(operations) == 2
                and operations[0] == operations[1]
            )
            state = (await live.a.get("/app/quintal")).json()["production"]
            other = (await live.other.get("/app/quintal")).json()["production"]
            assert (
                state["stock"]["apple"] == 6
                and state["stock"]["pear"] == 6
                and len(state["tasks"]) == 1
            )
            assert other["stock"]["apple"] == 9 and not other["tasks"]
            data = {
                "scenario": "BR-04",
                "api_arm": os.environ["QUALITY_API_ARM"],
                "client_arm": client_arm,
                "api_sha": manifest["sources"][os.environ["QUALITY_API_ARM"] + "-api"][
                    "sha"
                ],
                "client_sha": manifest["sources"][client_arm + "-flutter"]["sha"],
                "common_test_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
                "phases": ["before", "after"],
                "lost_response_after_commit": True,
                "same_idempotency_key_after_restart": True,
                "tasks": 1,
                "stock_apple": 6,
                "other_account_tasks": 0,
                "scope": "common service-level HTTP/SQLite contract; not phone UI or latency comparison",
            }
            (output / "observation.json").write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
        finally:
            server.close()
            await server.wait_closed()
