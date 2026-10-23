"""Capture/edit offline, reopen in new OS processes, then sync to an own API."""

import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse
from uuid import UUID

import pytest

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
ATTEMPT = Path(os.environ["QUALITY_CAMPAIGN_ATTEMPT"])
SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
assert SOURCE.is_relative_to(RUN / "sources")
sys.path.insert(0, str(SOURCE / "tests"))
pytest_plugins = ["test_quintal_live_api"]
from sqlalchemy import delete, select
from app.persistence_models import RemoteCat
from critical_report import flutter_tests
from subprocesses import run_logged


@pytest.mark.asyncio
async def test_RES01_IMG02_DATA01_offline_capture_edit_restart_sync_and_owner_isolation(
    live,
):
    assert (
        urlparse(live.url).hostname == "127.0.0.1" and urlparse(live.url).port != 8000
    )
    output = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "offline-flow"
    output.mkdir(parents=True, exist_ok=False)
    root = RUN / "sources/B/flutter"
    manifest = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    repo = Path(manifest["sources"]["B-flutter"]["repo"])
    for name in ["package_config.json", "package_graph.json"]:
        shutil.copy2(repo / ".dart_tool" / name, root / ".dart_tool" / name)
    tool = ATTEMPT / "executors/campaign_offline_capture_test.dart"
    shutil.copy2(tool, root / "test" / tool.name)
    database, documents = output / "sqlite", output / "documents"
    database.mkdir()
    documents.mkdir()
    receipt = output / "cats.json"
    phases = []
    try:
        for phase in ["capture", "reopen", "sync", "other", "final"]:
            client = live.other if phase == "other" else live.a
            options = {
                "phase": phase,
                "database": str(database),
                "documents": str(documents),
                "photo": str(RUN / "fixtures/synthetic.png"),
                "receipt": str(receipt),
                "account": {
                    "id": str(live.other_uid if phase == "other" else live.uid),
                    "token": client.headers["authorization"].removeprefix("Bearer "),
                },
                "output": str(output / (phase + ".json")),
            }
            with (output / (phase + ".jsonl")).open("w", encoding="utf-8") as log:
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
                    env=dict(os.environ, QUALITY_OFFLINE_FLOW=json.dumps(options)),
                    log=log,
                    timeout=240,
                )
            cases = flutter_tests(output / (phase + ".jsonl"))
            assert (
                result.returncode == 0
                and len(cases) == 1
                and not cases[0].get("skipped")
            ), "Inspect private phase log"
            phases.append(
                json.loads((output / (phase + ".json")).read_text(encoding="utf-8"))
            )
        cats = json.loads(receipt.read_text(encoding="utf-8"))
        async with live.db() as session:
            rows = (
                (
                    await session.execute(
                        select(RemoteCat).where(RemoteCat.user_id == live.uid)
                    )
                )
                .scalars()
                .all()
            )
            assert {str(row.client_id) for row in rows} == {cat["id"] for cat in cats}
            assert {row.payload["name"] for row in rows} == {
                cat["name"] for cat in cats
            }
            assert all(
                (row.analysis or {}).get("analysis_status", "pending") == "pending"
                and (row.analysis or {}).get("coat_type") is None
                and (row.analysis or {}).get("analyzed_at") is None
                for row in rows
            ), "Pending placeholders must not become completed model results"
            photo_updates = {
                str(row.client_id): (
                    row.client_updated_at + timedelta(seconds=1)
                ).isoformat()
                for row in rows
            }
        for cat in cats:
            path = "/sync/cats/" + cat["id"] + "/photo"
            assert (await live.a.get(path)).status_code == 404, (
                "Primary upload is deferred while analysis is disabled"
            )
            uploaded = await live.a.put(
                path,
                data={
                    "updated_at": photo_updates[cat["id"]],
                    "device_id": "explicit-upload-control",
                },
                files={
                    "image": (
                        "synthetic.png",
                        (RUN / "fixtures/synthetic.png").read_bytes(),
                        "image/png",
                    )
                },
            )
            assert uploaded.status_code == 204
            owner = await live.a.get(path)
            stranger = await live.other.get(path)
            assert (
                owner.status_code == 200
                and owner.content == (RUN / "fixtures/synthetic.png").read_bytes()
            )
            assert stranger.status_code == 404
        summary = {
            "scenario": "RES-01",
            "related": ["IMG-02", "DATA-01", "SEC-02"],
            "phases": phases,
            "server_owned_records": 2,
            "owner_photo_status": 200,
            "other_photo_status": 404,
            "client_analysis_disabled": True,
            "pending_analysis_placeholder_preserved": True,
            "primary_photo_deferred_before_explicit_http_control": True,
            "executor_sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
            "scope": "five desktop Flutter OS processes, real files/SQLite and disposable API/PG; session/platform adapters simulated; inference/geolocation disabled; private photo positive/negative controls use explicit HTTP upload after desktop phases, not automatic pending-analysis upload; no physical phone/background termination",
        }
        (output.parent / "RES01-offline-capture.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        if receipt.exists():
            ids = [
                UUID(cat["id"])
                for cat in json.loads(receipt.read_text(encoding="utf-8"))
            ]
            async with live.db() as session:
                await session.execute(
                    delete(RemoteCat).where(
                        RemoteCat.user_id == live.uid, RemoteCat.client_id.in_(ids)
                    )
                )
                await session.commit()
