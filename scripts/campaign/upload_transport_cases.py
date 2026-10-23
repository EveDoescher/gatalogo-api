"""Bounded real TCP upload interruptions at three distinct transaction windows."""

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image
import pytest
from sqlalchemy import delete, func, select, text

RUN = Path(os.environ["QUALITY_CAMPAIGN_RUN"])
SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
assert SOURCE.is_relative_to(RUN / "sources")
sys.path.insert(0, str(SOURCE / "tests"))
pytest_plugins = ["test_quintal_live_api"]
from app.persistence_models import CatPhoto, RemoteCat, SyncChange


def multipart(image, at):
    boundary = "campaign-owned-upload-boundary"
    fields = {"updated_at": at.isoformat(), "device_id": "owned-upload-control"}
    body = b""
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="synthetic.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
    body += image + f"\r\n--{boundary}--\r\n".encode()
    return body, "multipart/form-data; boundary=" + boundary


async def state(live, cat_id):
    async with live.db() as session:
        row = await session.get(RemoteCat, cat_id)
        photos = await session.scalar(
            select(func.count()).select_from(CatPhoto).where(CatPhoto.cat_id == cat_id)
        )
        revisions = await session.scalar(
            select(func.count())
            .select_from(SyncChange)
            .where(SyncChange.cat_id == cat_id)
        )
        return {
            "hash": row.photo_hash,
            "key": row.photo_key,
            "primary_rows": photos,
            "revisions": revisions,
        }


async def send_raw(live, path, body, content_type, partial=False):
    url = urlparse(live.url)
    _, writer = await asyncio.open_connection("127.0.0.1", url.port)
    header = f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1:{url.port}\r\nAuthorization: {live.a.headers['authorization']}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
    writer.write(header + (body[: len(body) // 2] if partial else body))
    await writer.drain()
    return writer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "window", ["partial-body", "disconnect-before-commit", "lost-response-after-commit"]
)
async def test_RES02_DATA02_real_upload_transport_windows_preserve_atomic_file_and_retry(
    live, window
):
    url = urlparse(live.url)
    assert url.hostname == "127.0.0.1" and url.port != 8000
    cat_id, client_id = uuid4(), uuid4()
    at = datetime.now(UTC) - timedelta(seconds=5)
    async with live.db() as session:
        session.add(
            RemoteCat(
                id=cat_id,
                client_id=client_id,
                user_id=live.uid,
                payload={"status": "pending_analysis"},
                client_updated_at=at,
                updated_by_device="seed",
            )
        )
        await session.commit()
    path = f"/sync/cats/{client_id}/photo"
    old = (RUN / "fixtures/synthetic.png").read_bytes()
    buffer = BytesIO()
    Image.new("RGB", (256, 256), (173, 81, 37)).save(buffer, format="PNG")
    new = buffer.getvalue()
    old_hash, new_hash = (
        hashlib.sha256(old).hexdigest(),
        hashlib.sha256(new).hexdigest(),
    )
    at += timedelta(seconds=1)
    old_body, content_type = multipart(old, at)
    proxy = None
    try:
        assert (
            await live.a.put(
                path, content=old_body, headers={"Content-Type": content_type}
            )
        ).status_code == 204
        baseline = await state(live, cat_id)
        assert baseline["hash"] == old_hash and baseline["primary_rows"] == 1
        body, content_type = multipart(new, at + timedelta(seconds=1))
        facts = {"window": window, "scenario": "RES-02", "before": baseline}
        if window == "partial-body":
            writer = await send_raw(live, path, body, content_type, partial=True)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
            observed = await state(live, cat_id)
            assert observed == baseline
            facts["partial_body_left_committed_state_unchanged"] = True
        elif window == "disconnect-before-commit":
            async with live.db() as barrier:
                await barrier.execute(
                    select(RemoteCat).where(RemoteCat.id == cat_id).with_for_update()
                )
                writer = await send_raw(live, path, body, content_type)
                try:
                    deadline = asyncio.get_running_loop().time() + 5
                    blocked = False
                    while asyncio.get_running_loop().time() < deadline:
                        async with live.db() as inspect:
                            blocked = bool(
                                await inspect.scalar(
                                    text(
                                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query ILIKE '%FROM cats%' AND query ILIKE '%FOR UPDATE%'"
                                    )
                                )
                            )
                        if blocked:
                            break
                        await asyncio.sleep(0.05)
                    assert blocked, (
                        "DB lock is the pre-commit barrier, not an arbitrary delay"
                    )
                    assert (await state(live, cat_id))["hash"] == old_hash
                    writer.close()
                    await writer.wait_closed()
                finally:
                    if not writer.is_closing():
                        writer.close()
                        await writer.wait_closed()
                    await barrier.rollback()
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                observed = await state(live, cat_id)
                if observed["hash"] == new_hash:
                    break
                await asyncio.sleep(0.05)
            assert observed["hash"] in {old_hash, new_hash}
            facts["precommit_lock_observed"] = True
            facts["disconnect_outcome_before_retry"] = (
                "COMMITTED" if observed["hash"] == new_hash else "UNCHANGED"
            )
        else:
            accepted = asyncio.Event()
            forwarded = []
            tasks = set()

            async def drop(reader, writer):
                task = asyncio.current_task()
                tasks.add(task)
                try:
                    header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                    length = next(
                        int(line.split(b":", 1)[1])
                        for line in header.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    )
                    data = await asyncio.wait_for(reader.readexactly(length), 5)
                    async with httpx.AsyncClient(
                        base_url=live.url, timeout=10
                    ) as upstream:
                        response = await upstream.put(
                            path,
                            content=data,
                            headers={
                                "Authorization": live.a.headers["authorization"],
                                "Content-Type": content_type,
                            },
                        )
                    forwarded.append(response.status_code)
                    accepted.set()
                    writer.transport.abort()  # Drop only this owned client's response after upstream commit.
                finally:
                    writer.close()
                    await writer.wait_closed()
                    tasks.discard(task)

            proxy = await asyncio.start_server(drop, "127.0.0.1", 0)
            port = proxy.sockets[0].getsockname()[1]
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    with pytest.raises(httpx.RemoteProtocolError):
                        await client.put(
                            f"http://127.0.0.1:{port}" + path,
                            content=body,
                            headers={"Content-Type": content_type},
                        )
                await asyncio.wait_for(accepted.wait(), 5)
                assert forwarded == [204]
                observed = await state(live, cat_id)
                assert observed["hash"] == new_hash
                facts["upstream_committed_before_response_drop"] = True
            finally:
                proxy.close()
                await proxy.wait_closed()
                if tasks:
                    await asyncio.gather(*tasks)
        facts["after_interruption"] = observed
        assert (
            await live.a.put(path, content=body, headers={"Content-Type": content_type})
        ).status_code == 204
        final = await state(live, cat_id)
        assert final["hash"] == new_hash and final["primary_rows"] == 1
        response = await live.a.get(path)
        assert response.status_code == 200 and response.content == new
        assert (await live.other.get(path)).status_code == 404
        assert (await live.a.get("/health")).status_code == 200
        facts.update(
            after_retry=final,
            owner_photo=200,
            other_photo=404,
            scope="real bounded loopback TCP/HTTP/PG; own photos and pending-analysis state, no inference. Revisions recorded rather than assumed exactly-once; primary/file-response integrity is the oracle, no full orphan-file inventory.",
        )
        output = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / (
            "RES02-" + window + ".json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        assert not output.exists()
        output.write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    finally:
        if proxy:
            proxy.close()
            await proxy.wait_closed()
        async with live.db() as session:
            await session.execute(
                delete(RemoteCat).where(
                    RemoteCat.id == cat_id, RemoteCat.user_id == live.uid
                )
            )
            await session.commit()
