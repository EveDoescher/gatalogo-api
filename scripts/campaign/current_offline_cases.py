"""Current Flutter OS processes against an own API with deterministic local inference."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import uvicorn

pytest.register_assert_rewrite("security_cases", "image_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence
from image_cases import configured, image_bytes, semantic
from critical_report import flutter_tests
from subprocesses import run_logged
from app import main
from app.persistence_models import HabitatWallet, RemoteCat, User
from app.services.auth_service import issue_tokens
from app.services.fcm_service import fcm_sender


def client_hashes(repo):
    paths = [repo / "pubspec.yaml", repo / "pubspec.lock"]
    for folder in ("lib", "test", "tool"):
        paths.extend((repo / folder).rglob("*.dart"))
    return {
        p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


@pytest_asyncio.fixture
async def own_api(tmp_path, monkeypatch):
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
    ids = [uuid4(), uuid4()]
    async with factory() as session:
        users = [
            User(
                id=ident, email=f"offline-{ident.hex}@example.invalid", status="active"
            )
            for ident in ids
        ]
        session.add_all(users)
        await session.flush()
        for user in users:
            session.add(HabitatWallet(user_id=user.id, balance=100))
        bundles = [
            await issue_tokens(session, user=user, device_id="offline-fixture")
            for user in users
        ]
        await session.commit()
    service = configured(tmp_path, monkeypatch)
    calls = []

    def provider(*args):
        calls.append(1)
        return semantic()

    monkeypatch.setattr(service, "_analyze_with_gemini", provider)
    monkeypatch.setattr(main, "AnalysisService", lambda _: service)
    monkeypatch.setattr(
        fcm_sender,
        "send_notification",
        AsyncMock(return_value={"delivered": [], "retry": False}),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    assert port != 8000
    server = uvicorn.Server(
        uvicorn.Config(
            main.app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            access_log=False,
            timeout_keep_alive=1,
            timeout_graceful_shutdown=3,
        )
    )
    pending = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            assert not pending.done()
            await asyncio.sleep(0.05)
        assert server.started
        yield SimpleNamespace(
            url=f"http://127.0.0.1:{port}",
            factory=factory,
            ids=ids,
            bundles=bundles,
            provider_calls=calls,
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(pending, 10)
        listener.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_RES01_IMG02_current_capture_analyze_edit_restart_and_account_isolation(
    own_api,
):
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "current-offline"
    folder.mkdir(parents=True, exist_ok=False)
    repo = Path(os.environ["QUALITY_FLUTTER_REPO"]).resolve()
    assert repo.name == "catlogue"
    before = client_hashes(repo)
    manifest = {
        "flutter_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip(),
        "evaluated_content_sha256": before,
        "api_url_scope": "own loopback random port",
        "physical_device": False,
    }
    (folder / "client-source-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    sdk = Path(os.environ.get("FLUTTER_SDK", "C:/flutter"))
    database, documents = folder / "sqlite", folder / "documents"
    database.mkdir()
    documents.mkdir()
    photo = folder / "synthetic.png"
    photo.write_bytes(image_bytes())
    receipt = folder / "cats.json"
    phases, analysis_before_metadata = [], None
    for phase in ("capture", "reopen", "sync", "metadata", "other", "final"):
        actor = 1 if phase == "other" else 0
        options = {
            "phase": phase,
            "analysis_enabled": True,
            "database": str(database),
            "documents": str(documents),
            "photo": str(photo),
            "receipt": str(receipt),
            "account": {
                "id": str(own_api.ids[actor]),
                "token": own_api.bundles[actor]["access_token"],
            },
            "output": str(folder / (phase + ".json")),
        }
        with (folder / (phase + ".jsonl")).open("w", encoding="utf-8") as log:
            result = await asyncio.to_thread(
                run_logged,
                [
                    str(sdk / "bin/cache/dart-sdk/bin/dart.exe"),
                    str(sdk / "bin/cache/flutter_tools.snapshot"),
                    "test",
                    "--no-pub",
                    "--machine",
                    "--timeout=100s",
                    f"--dart-define=API_BASE_URL={own_api.url}",
                    "tool/campaign_offline_capture_test.dart",
                ],
                cwd=repo,
                env=dict(os.environ, QUALITY_OFFLINE_FLOW=json.dumps(options)),
                log=log,
                timeout=120 if phase in ("capture", "sync") else 60,
            )
        cases = flutter_tests(folder / (phase + ".jsonl"))
        assert (
            result.returncode == 0
            and len(cases) == 1
            and cases[0]["result"] == "success"
            and not cases[0]["skipped"]
        ), f"Inspect private {phase} phase log"
        phases.append(
            json.loads((folder / (phase + ".json")).read_text(encoding="utf-8"))
        )
        if phase in ("sync", "metadata"):
            async with own_api.factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(RemoteCat).where(RemoteCat.user_id == own_api.ids[0])
                        )
                    ).all()
                )
                assert len(rows) == 2 and all(
                    r.analysis and r.payload["status"] == "completed" for r in rows
                )
                current = {str(r.client_id): r.analysis for r in rows}
            if phase == "sync":
                analysis_before_metadata = current
            else:
                assert current == analysis_before_metadata
    expected = json.loads(receipt.read_text(encoding="utf-8"))
    async with own_api.factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(RemoteCat).where(RemoteCat.user_id == own_api.ids[0])
                )
            ).all()
        )
        assert {r.payload["name"] for r in rows} == {"Lua revista", "Praça revista"}
        assert {str(r.client_id) for r in rows} == {r["id"] for r in expected}
    async with httpx.AsyncClient(base_url=own_api.url) as client:
        for row in expected:
            path = "/sync/cats/" + row["id"] + "/photo"
            owner = await client.get(
                path,
                headers={
                    "Authorization": "Bearer " + own_api.bundles[0]["access_token"]
                },
            )
            foreign = await client.get(
                path,
                headers={
                    "Authorization": "Bearer " + own_api.bundles[1]["access_token"]
                },
            )
            assert owner.status_code == 200 and owner.content == photo.read_bytes()
            assert foreign.status_code == 404
    assert 0 < len(own_api.provider_calls) <= 2
    assert client_hashes(repo) == before
    evidence(
        "RES01-IMG02-current-client",
        {
            "scenarios": ["RES-01", "IMG-02"],
            "phases": phases,
            "separate_flutter_os_processes": 6,
            "client_sources_unchanged": True,
            "flutter_sha": manifest["flutter_sha"],
            "real_loopback_http": True,
            "inference": "local deterministic fake, actual preprocessing/cache/fusion",
            "paid_calls": 0,
            "metadata_preserves_authoritative_analysis": True,
            "primary_photos_created_by_client_analysis_flow": 2,
            "owner_photo": 200,
            "foreign_photo": 404,
            "scope": "Desktop real CatStore/SQLite/files and own migrated PostgreSQL/API; network availability and session/platform adapters controlled, no phone radio/background/UI certification.",
        },
    )
