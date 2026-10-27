"""Own storage fault injection and short pause/resume of this runner's DB only."""

import asyncio
from datetime import timedelta
import errno
from io import BytesIO
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock

from PIL import Image
import pytest
from sqlalchemy.engine import make_url

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases", "test_quintal_live_api"]
from security_cases import evidence, uid, NOW, SOURCE

sys.path.insert(0, str(SOURCE / "tests"))
from app.persistence_models import CatPhoto, RemoteCat
from app.services.storage_service import photo_storage
from app.routers import sync


async def own_primary(db):
    old = b"previous photo bytes"
    async with db.factory() as session:
        cat = RemoteCat(
            id=uid("res-storage-cat"),
            client_id=uid("res-storage-client"),
            user_id=uid("A"),
            payload={"status": "pending_analysis"},
            client_updated_at=NOW,
            updated_by_device="a",
        )
        session.add(cat)
        await session.flush()
        key, digest = db.storage.save(
            user_id=uid("A"), cat_id=cat.id, data=old, suffix=".png"
        )
        cat.photo_key, cat.photo_hash, cat.photo_content_type = key, digest, "image/png"
        session.add(
            CatPhoto(
                cat_id=cat.id,
                kind="primary",
                storage_key=key,
                content_hash=digest,
                content_type="image/png",
                status="ready",
            )
        )
        await session.commit()
    out = BytesIO()
    Image.new("RGB", (128, 128), (20, 50, 80)).save(out, format="PNG")
    return old, key, out.getvalue()


async def upload(db, data):
    return await db.client.put(
        f"/sync/cats/{uid('res-storage-client')}/photo",
        headers=db.headers("A"),
        data={"updated_at": (NOW + timedelta(seconds=1)).isoformat(), "device_id": "a"},
        files={"image": ("photo.png", data, "image/png")},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["no-space", "permission"])
async def test_RES05_IMG07_storage_write_failure_rollback_and_retry(
    db, monkeypatch, kind
):
    old, key, data = await own_primary(db)
    real = photo_storage._write

    def fail(*args, **kwargs):
        raise OSError(
            errno.ENOSPC if kind == "no-space" else errno.EACCES,
            "controlled own storage fault",
        )

    monkeypatch.setattr(photo_storage, "_write", fail)
    with pytest.raises(OSError):
        await upload(db, data)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("res-storage-cat"))
        assert cat.photo_key == key
    assert db.storage.open(key).read_bytes() == old
    monkeypatch.setattr(photo_storage, "_write", real)
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    retry = await upload(db, data)
    assert retry.status_code == 204
    read = await db.client.get(
        f"/sync/cats/{uid('res-storage-client')}/photo", headers=db.headers("A")
    )
    assert read.status_code == 200 and read.content == data
    evidence(
        "RES05-storage-" + kind,
        {
            "scenario": "RES-05",
            "fault": kind,
            "database_rollback": True,
            "previous_file_intact": True,
            "retry": 204,
            "owner_read": 200,
            "scope": "injected OS exception in private storage, no disk filling or ACL changes",
        },
    )


@pytest.mark.asyncio
async def test_RES05_RSC03_delete_failure_after_commit_recovery(
    db, monkeypatch
):
    old, key, data = await own_primary(db)
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    real = photo_storage.delete

    def fail(*args, **kwargs):
        raise PermissionError("controlled cleanup failure")

    monkeypatch.setattr(photo_storage, "delete", fail)
    assert (await upload(db, data)).status_code == 204
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("res-storage-cat"))
        newkey = cat.photo_key
        assert newkey != key and db.storage.open(newkey).read_bytes() == data
    assert db.storage.open(key).read_bytes() == old
    pending = list(db.storage.absolute_path(".photo-cleanup").glob("*.json"))
    assert len(pending) == 1
    monkeypatch.setattr(photo_storage, "delete", real)
    from app.services.photo_cleanup import PhotoCleanupWorker

    worker = PhotoCleanupWorker(db.factory, interval=0.05)
    worker.start()
    try:
        for _ in range(60):
            if not pending[0].exists():
                break
            await asyncio.sleep(0.05)
        assert not pending[0].exists()
    finally:
        await worker.stop()
    assert (await upload(db, data)).status_code == 204
    orphan = photo_storage.absolute_path(key).is_file()
    assert not orphan and db.storage.open(newkey).read_bytes() == data
    evidence(
        "RSC03-postcommit-cleanup",
        {
            "scenario": "RSC-03",
            "new_content_committed_despite_error": True,
            "retry": 204,
            "old_file_remains_after_retry": orphan,
            "deferred_candidate_persisted": True,
            "recovered_by_new_worker_without_client_retry": True,
            "scope": "own files, injected delete error and restarted cleanup task; no power-loss claim",
        },
    )


@pytest.mark.asyncio
async def test_IMG07_missing_private_file_then_restored(db):
    old, key, _ = await own_primary(db)
    path = db.storage.open(key)
    path.unlink()
    try:
        unavailable = await db.client.get(
            f"/sync/cats/{uid('res-storage-client')}/photo", headers=db.headers("A")
        )
        assert unavailable.status_code == 404
    finally:
        path.write_bytes(old)
    response = await db.client.get(
        f"/sync/cats/{uid('res-storage-client')}/photo", headers=db.headers("A")
    )
    assert response.status_code == 200 and response.content == old
    evidence(
        "IMG07-storage-restore",
        {
            "scenario": "IMG-07",
            "missing_file_http_status": 404,
            "restored_read": 200,
            "scope": "own file; owner endpoint returns controlled 404; device UX is separate",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("client_timeout", [False, True])
async def test_RES05_disposable_database_short_outage_and_recovery(live, client_timeout):
    # Locate by the exact random host port AND runner purpose label before pausing.
    url = make_url(os.environ["RECOGNITION_TEST_DATABASE_URL"])
    assert (
        url.host == "127.0.0.1"
        and url.port != 5432
        and url.database == "recognition_test"
    )
    ids = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=gatalogo.purpose=quality-integration",
        ],
        text=True,
    ).split()
    matches = []
    for ident in ids:
        item = json.loads(
            subprocess.check_output(["docker", "inspect", ident], text=True)
        )[0]
        ports = item["NetworkSettings"]["Ports"].get("5432/tcp") or []
        if any(int(p["HostPort"]) == url.port for p in ports):
            matches.append(ident)
    assert len(matches) == 1
    ident = matches[0]
    assert (await live.a.get("/sync/pull")).status_code == 200
    paused = False
    request = None
    started = None
    timeout_seconds = None
    try:
        subprocess.run(
            ["docker", "pause", ident], check=True, stdout=subprocess.DEVNULL
        )
        paused = True
        import time
        started = time.perf_counter()
        request = asyncio.create_task(live.a.get("/sync/pull"))
        await asyncio.sleep(0.5)
        assert not request.done()
        assert (await live.other.get("/health")).status_code == 200
        if client_timeout:
            import httpx
            with pytest.raises(httpx.ReadTimeout):
                await asyncio.wait_for(request, 14)
            timeout_seconds = time.perf_counter() - started
            assert 9 <= timeout_seconds < 14
            request = None
    finally:
        if paused:
            subprocess.run(
                ["docker", "unpause", ident], check=True, stdout=subprocess.DEVNULL
            )
        if request is not None:
            resumed = await asyncio.wait_for(request, 20)
    if not client_timeout:
        assert resumed.status_code == 200
    assert (await live.a.get("/sync/pull")).status_code == 200
    assert (await live.a.get("/app/quintal")).status_code == 200
    evidence(
        "RES05-real-db-pause-" + ("client-timeout" if client_timeout else "short"),
        {
            "scenario": "RES-05",
            "outage": "short pause of exact random-port disposable DB container",
            "authenticated_request_pending_during_pause": True,
            "health_during_pause": 200,
            "after_resume": 200,
            "quintal_integrity_read": 200,
            "client_timeout_seconds": timeout_seconds,
            "scope": "Own server stall/recovery and actual 10s HTTP client timeout; no crash/data-loss or API SQL deadline certification",
        },
    )
