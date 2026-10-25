"""Synthetic canaries in actual provider failures and bounded local Uvicorn logs."""

import asyncio
import base64
import logging
import os
from pathlib import Path
import socket as socket_module
import sys
import traceback
from types import SimpleNamespace

import pytest

pytest.register_assert_rewrite("security_cases", "image_cases", "extended_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, SOURCE, uid
from image_cases import configured
from extended_cases import seed
from test_auth_security import Socket
from app.config import get_settings
from app.exceptions import GeminiAnalysisError
from app.routers import websocket


def test_SEC08_terminal_provider_error_has_no_secret_log_or_trace(
    tmp_path, monkeypatch, caplog
):
    service = configured(tmp_path, monkeypatch)
    service.settings.gemini_max_retries = 0
    canary = service.settings.gemini_api_key

    def failed(**kwargs):
        raise RuntimeError("controlled upstream key=" + canary)

    client = SimpleNamespace(models=SimpleNamespace(generate_content=failed))
    with caplog.at_level(logging.ERROR, logger="app.services.analysis_service"):
        with pytest.raises(GeminiAnalysisError) as caught:
            service._generate_content_with_retry(client, contents=[], config=None)
    assert canary not in caplog.text
    assert canary not in "".join(traceback.format_exception(caught.value))
    evidence(
        "SEC08-terminal-provider",
        dict(fake_provider=True, secret_in_log=False, secret_in_trace=False),
    )


@pytest.mark.asyncio
async def test_SEC08_auth_upload_and_websocket_error_canaries(db, monkeypatch, caplog):
    await seed(db)
    token_canary = "INVALID_TOKEN_CANARY_PRIVATE"
    body_canary = b"PRIVATE_PHOTO_BODY_CANARY"
    monkeypatch.setattr(get_settings(), "max_image_bytes", 4)
    monkeypatch.setattr(websocket, "get_session_factory", lambda: db.factory)
    with caplog.at_level(logging.INFO):
        auth = await db.client.get(
            "/auth/me", headers={"Authorization": "Bearer " + token_canary}
        )
        assert auth.status_code == 401 and token_canary not in auth.text
        upload = await db.client.put(
            f"/sync/cats/{uid('matrix-client')}/photo",
            headers=db.headers("B"),
            data={"updated_at": "2026-10-24T12:00:00Z", "device_id": "synthetic"},
            files={"image": ("PRIVATE_FILENAME_CANARY.png", body_canary, "image/png")},
        )
        assert upload.status_code == 413
        async with Socket(token_canary) as denied:
            assert (await denied.event())["code"] == 1008
    for secret in [
        token_canary,
        body_canary.decode(),
        "PRIVATE_FILENAME_CANARY",
        *db.tokens.values(),
    ]:
        assert secret not in caplog.text
    evidence(
        "SEC08-error-canaries",
        dict(
            auth=401,
            oversize_upload=413,
            invalid_ws=1008,
            canaries_in_errors_or_logs=False,
            scope="actual controlled error paths, no real credentials",
        ),
    )


@pytest.mark.asyncio
async def test_SEC08_real_uvicorn_websocket_access_log_redacts_query_credentials():
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "SEC08-uvicorn"
    folder.mkdir(parents=True, exist_ok=False)
    probe = socket_module.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    canary = "WS_QUERY_SECRET_CANARY_NOT_REAL"
    path = folder / "uvicorn.log"
    with path.open("w", encoding="utf-8") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lifespan",
            "off",
            "--log-level",
            "info",
            "--access-log",
            cwd=SOURCE,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if process.returncode is not None:
                    pytest.fail(
                        "Disposable Uvicorn failed startup; inspect private log"
                    )
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("Disposable Uvicorn did not become ready")
            for parameter in ("token", "%74oken", "access_token"):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                key = base64.b64encode(b"0123456789abcdef").decode()
                request = (
                    f"GET /ws?{parameter}={canary}&safe=VISIBLE_QUERY_CONTEXT HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                )
                writer.write(request.encode())
                await writer.drain()
                response = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), timeout=5
                )
                assert response.startswith(b"HTTP/1.1 403")
                writer.close()
                await writer.wait_closed()
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
    logs = path.read_text(encoding="utf-8", errors="replace")
    assert "WebSocket" in logs and "safe=VISIBLE_QUERY_CONTEXT" in logs
    evidence(
        "SEC08-uvicorn-query",
        dict(
            real_tcp=True,
            real_uvicorn=True,
            query_variants=["token", "percent-encoded token name", "access_token"],
            synthetic_credential_in_access_log=canary in logs,
            lifespan=False,
            scope="own loopback server/PG and invalid synthetic credentials, no app or user service restart",
        ),
    )
    assert canary not in logs
