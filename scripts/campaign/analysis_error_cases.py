"""LAT-03 with real Flutter multipart/auth clients, TCP and own PostgreSQL."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from starlette.responses import JSONResponse, Response

pytest_plugins = ["current_offline_cases"]
from current_offline_cases import client_hashes
from critical_report import flutter_tests
from image_cases import image_bytes
from security_cases import evidence
from subprocesses import run_logged
from app import main
from app.exceptions import MissingApiKeyError, RejectedImageError
from app.persistence_models import CatPhoto, RemoteCat, User, VisionJob
from app.services.auth_service import issue_tokens


@pytest.fixture(autouse=True)
def trace_app(monkeypatch):
    app = main.app
    state = {"variant": None, "requests": [], "cases": [], "secret": secrets.token_hex(24)}

    class Trace:
        def __getattr__(self, name):
            return getattr(app, name)

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            path = scope["path"]
            if path == "/__quality__/analysis-case":
                if dict(scope["headers"]).get(b"x-quality-secret", b"").decode() != state["secret"]:
                    return await Response(status_code=403)(scope, receive, send)
                body = b""
                while True:
                    chunk = await receive()
                    body += chunk.get("body", b"")
                    if not chunk.get("more_body"):
                        break
                options = json.loads(body)
                state["variant"] = options["variant"]
                async with state["factory"]() as session:
                    user = await session.get(User, state["owner"])
                    # AuthTokens validates EmailStr; .invalid is rejected even
                    # though the original fixture only needed JWT-protected reads.
                    user.email = f"analysis-errors-{user.id.hex}@example.com"
                    bundle = await issue_tokens(session, user=user, device_id="analysis-errors-fixture")
                    client_id, cat_id = uuid4(), uuid4()
                    session.add(RemoteCat(id=cat_id, user_id=user.id, client_id=client_id,
                        payload={"status": "pending_analysis", "is_owned": True},
                        client_updated_at=datetime.now(UTC), updated_by_device="lat03-fixture"))
                    await session.commit()
                if state["variant"].startswith("refresh-") or state["variant"] == "repeated-401":
                    # Preserve future expiry so the explicit server 401 is exercised.
                    header, claims, _ = bundle["access_token"].split(".")
                    bundle["access_token"] = f"{header}.{claims}.controlled-invalid-signature"
                state["cases"].append({"variant": state["variant"], "client_id": client_id,
                    "cat_id": cat_id, "measured": options["measured"], "requests": [], "analyzed_hashes": []})
                return await JSONResponse({**bundle, "user_id": str(bundle["user_id"]), "client_id": str(client_id)})(scope, receive, send)
            started = time.perf_counter()
            status = None

            async def record(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            if path == "/analyze" and state["variant"] == "unavailable-html":
                await Response("<html>temporary gateway outage</html>", status_code=503)(scope, receive, record)
            elif path == "/analyze" and state["variant"] == "repeated-401":
                await JSONResponse({"detail": "Credenciais inválidas."}, status_code=401)(scope, receive, record)
            else:
                await app(scope, receive, record)
            if path in ("/analyze", "/auth/refresh") and state["cases"]:
                item = {"path": path, "status": status, "elapsed_ms": (time.perf_counter() - started) * 1000}
                state["cases"][-1]["requests"].append(item)
    monkeypatch.setattr(main, "app", Trace())
    return state


@pytest.mark.asyncio
async def test_LAT03_real_clients_error_refresh_and_replay_bounds(own_api, trace_app, monkeypatch):
    state = trace_app
    state.update(factory=own_api.factory, owner=own_api.ids[0])
    working = main.AnalysisService(None)

    class Controlled:
        async def analyze(self, **kwargs):
            state["cases"][-1]["analyzed_hashes"].append(hashlib.sha256(kwargs["image_bytes"]).hexdigest())
            if state["variant"] == "rejected":
                raise RejectedImageError(reason="NOT_A_CAT", message="Nenhum gato foi identificado na imagem.")
            if state["variant"] == "unavailable-json":
                raise MissingApiKeyError("Controlled disposable outage")
            return await working.analyze(**kwargs)
    monkeypatch.setattr(main, "AnalysisService", lambda _: Controlled())
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    folder.mkdir(parents=True, exist_ok=True)
    photo = folder / "synthetic.png"
    photo.write_bytes(image_bytes())
    expected_hash = hashlib.sha256(photo.read_bytes()).hexdigest()
    repo = Path(os.environ["QUALITY_FLUTTER_REPO"])
    before = client_hashes(repo)
    (folder / "client-source.json").write_text(json.dumps({"sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(), "content_sha256": before}, indent=2))
    config = {"secret": state["secret"], "photo": str(photo), "result": str(folder / "client-results.json")}

    def execute():
        with (folder / "flutter.jsonl").open("w", encoding="utf-8") as log:
            return run_logged(["C:/flutter/bin/cache/dart-sdk/bin/dart.exe", "C:/flutter/bin/cache/flutter_tools.snapshot",
                "test", "--no-pub", "--machine", "--timeout=100s",
                f"--dart-define=API_BASE_URL={own_api.url}", "tool/campaign_analysis_errors_test.dart"],
                cwd=repo, env=dict(os.environ, QUALITY_ANALYSIS_ERRORS_CONFIG=json.dumps(config)), log=log, timeout=240)
    result = await asyncio.to_thread(execute)
    assert before == client_hashes(repo), "Client sources changed during experiment"
    tests = flutter_tests(folder / "flutter.jsonl")
    (folder / "server-cases.json").write_text(json.dumps(state["cases"], indent=2, default=str))
    assert result.returncode == 0 and len(tests) == 7 and all(t["result"] == "success" and not t["skipped"] for t in tests), "Inspect private Flutter log"
    rows = json.loads((folder / "client-results.json").read_text())
    assert len(rows) == len(state["cases"]) == 42
    signatures = {"rejected": [422], "unavailable-json": [503], "unavailable-html": [503],
        "refresh-success": [401, 200], "refresh-failed": [401], "repeated-401": [401, 401], "signed-out": []}
    for case in state["cases"]:
        analyses = [r["status"] for r in case["requests"] if r["path"] == "/analyze"]
        assert analyses == signatures[case["variant"]], case
        refresh = [r["status"] for r in case["requests"] if r["path"] == "/auth/refresh"]
        assert refresh == ([401] if case["variant"] == "refresh-failed" else [200] if case["variant"] in ("refresh-success", "repeated-401") else []), case
        assert all(digest == expected_hash for digest in case["analyzed_hashes"])
        async with own_api.factory() as session:
            cat = await session.get(RemoteCat, case["cat_id"])
            photos = await session.scalar(select(func.count()).select_from(CatPhoto).where(CatPhoto.cat_id == cat.id))
            jobs = await session.scalar(select(func.count()).select_from(VisionJob).where(VisionJob.cat_id == cat.id))
            if case["variant"] == "refresh-success":
                assert cat.analysis and cat.photo_hash == expected_hash and photos == jobs == 1
            else:
                assert cat.analysis is None and cat.photo_key is None and photos == jobs == 0
    evidence("LAT03-clients", {"scenario": "LAT-03", "client_tests": tests, "client_samples": rows,
        "server_cases": state["cases"], "measured_per_variant": 5, "warmups_per_variant": 1,
        "paid_calls": 0, "physical_device": False,
        "scope": "Real Flutter analysis/auth clients and own TCP/API/Postgres; injected availability/rejection only. Five samples/variant describe error completion, not reliable tail percentiles, SLA or speedup."})
