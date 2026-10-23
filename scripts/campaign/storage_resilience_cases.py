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
async def test_RES05_RSC03_delete_failure_after_commit_characterization(
    db, monkeypatch
):
    old, key, data = await own_primary(db)
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    real = photo_storage.delete

    def fail(*args, **kwargs):
        raise PermissionError("controlled cleanup failure")

    monkeypatch.setattr(photo_storage, "delete", fail)
    with pytest.raises(PermissionError):
        await upload(db, data)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("res-storage-cat"))
        newkey = cat.photo_key
        assert newkey != key and db.storage.open(newkey).read_bytes() == data
    assert db.storage.open(key).read_bytes() == old
    monkeypatch.setattr(photo_storage, "delete", real)
    assert (await upload(db, data)).status_code == 204
    orphan = photo_storage.absolute_path(key).is_file()
    evidence(
        "RSC03-postcommit-cleanup",
        {
            "scenario": "RSC-03",
            "new_content_committed_despite_error": True,
            "retry": 204,
            "old_file_remains_after_retry": orphan,
            "finding": "Cleanup failure happens after commit; retry of same content does not revisit old key"
            if orphan
            else None,
            "scope": "own files and injected delete error",
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
async def test_RES05_disposable_database_short_outage_and_recovery(live):
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
    try:
        subprocess.run(
            ["docker", "pause", ident], check=True, stdout=subprocess.DEVNULL
        )
        paused = True
        request = asyncio.create_task(live.a.get("/sync/pull"))
        await asyncio.sleep(0.5)
        assert not request.done()
        assert (await live.other.get("/health")).status_code == 200
    finally:
        if paused:
            subprocess.run(
                ["docker", "unpause", ident], check=True, stdout=subprocess.DEVNULL
            )
        if request is not None:
            resumed = await asyncio.wait_for(request, 20)
    assert resumed.status_code == 200
    assert (await live.a.get("/app/quintal")).status_code == 200
    evidence(
        "RES05-real-db-pause",
        {
            "scenario": "RES-05",
            "outage": "short pause of exact random-port disposable DB container",
            "authenticated_request_pending_during_pause": True,
            "health_during_pause": 200,
            "after_resume": 200,
            "quintal_integrity_read": 200,
            "scope": "connection/server stall and recovery, not crash/data-loss or timeout upper-bound certification",
        },
    )
