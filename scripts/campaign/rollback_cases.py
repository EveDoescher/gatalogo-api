"""Transaction and filesystem fault boundaries on disposable data only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases", "extended_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid
from extended_cases import seed
from app.persistence_models import (
    CatPhoto,
    RemoteCat,
    SyncChange,
    VisionJob,
    HabitatWallet,
)
from app.services import reference_analysis as worker
from app.services.realtime_events import queue_event
from app.services.storage_service import PrivatePhotoStorage
from app.services.vision_jobs import enqueue_reference_analysis


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["progression", "before-commit", "success"])
async def test_DATA02_worker_transaction_visibility(db, monkeypatch, boundary):
    await seed(db)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("matrix-cat"))
        photo = await session.scalar(
            select(CatPhoto).where(CatPhoto.cat_id == cat.id, CatPhoto.kind == "front")
        )
        photo.status, photo.analysis = "analyzing", None
        job = await enqueue_reference_analysis(session, cat=cat, photo=photo)
        photo_id, job_id = photo.id, job.id
        await session.commit()
    send = AsyncMock()
    from app.services import realtime_events

    monkeypatch.setattr(realtime_events.ws_manager, "send_to_user", send)

    async def assert_old_state():
        async with db.factory() as observer:
            assert (await observer.get(CatPhoto, photo_id)).analysis is None
            assert (await observer.get(HabitatWallet, uid("B"))).balance == 100
            assert (
                await observer.scalar(select(func.count()).select_from(SyncChange)) == 0
            )
        send.assert_not_awaited()

    async def progression(session, cat, photo, incoming, **kwargs):
        wallet = await session.get(HabitatWallet, cat.user_id)
        wallet.balance += 7
        cat.payload = {**cat.payload, "synthetic_reward": 7}
        queue_event(session, user_id=cat.user_id, event="synthetic_success", data={})
        await session.flush()
        await assert_old_state()
        if boundary == "progression":
            raise RuntimeError("controlled progression failure after flush")

    original_record = worker.record_change

    async def record(session, cat):
        await original_record(session, cat)
        await session.flush()
        await assert_old_state()
        if boundary == "before-commit":
            raise RuntimeError("controlled failure before commit")

    monkeypatch.setattr(worker, "confirm_photo", progression)
    monkeypatch.setattr(worker, "rebuild_analysis_from_photos", AsyncMock())
    monkeypatch.setattr(worker, "record_change", record)
    settings = SimpleNamespace(
        vision_job_max_attempts=3,
        vision_job_lease_seconds=30,
        max_image_bytes=10_000_000,
    )
    assert await worker.process_next(
        db.factory,
        settings=settings,
        analyzer=AsyncMock(return_value={"synthetic": "analysis"}),
    )
    async with db.factory() as observer:
        photo = await observer.get(CatPhoto, photo_id)
        wallet = await observer.get(HabitatWallet, uid("B"))
        cat = await observer.get(RemoteCat, uid("matrix-cat"))
        job = await observer.get(VisionJob, job_id)
        if boundary == "success":
            assert (
                photo.analysis == {"synthetic": "analysis"} and photo.status == "ready"
            )
            assert wallet.balance == 107 and cat.payload["synthetic_reward"] == 7
            assert job.status == "completed" and send.await_count > 0
        else:
            assert photo.analysis is None and photo.status == "analyzing"
            assert wallet.balance == 100 and "synthetic_reward" not in cat.payload
            assert job.status == "pending"
            assert (
                await observer.scalar(select(func.count()).select_from(SyncChange)) == 0
            )
            send.assert_not_awaited()
    evidence(
        "DATA02-worker-" + boundary,
        dict(
            boundary=boundary,
            separate_session_saw_only_committed_state=True,
            balance=wallet.balance,
            job_status=job.status,
            published_events=send.await_count,
            inference="synthetic stub",
        ),
    )


@pytest.mark.parametrize("existing", [False, True])
def test_DATA02_interrupted_write_preserves_destination(
    tmp_path, monkeypatch, existing
):
    storage = PrivatePhotoStorage(tmp_path / "private")
    key = "users/synthetic/avatar.png"
    destination = storage.absolute_path(key)
    destination.parent.mkdir(parents=True)
    if existing:
        destination.write_bytes(b"old-complete-body")
    from pathlib import Path

    original = Path.write_bytes

    def interrupted(path, data):
        original(path, data[:4])
        raise OSError("controlled partial write")

    monkeypatch.setattr(Path, "write_bytes", interrupted)
    with pytest.raises(OSError, match="controlled partial write"):
        storage._write(key, b"new-complete-body")
    assert (
        destination.read_bytes() == b"old-complete-body"
        if existing
        else not destination.exists()
    )
    assert list(destination.parent.iterdir()) == ([destination] if existing else [])
