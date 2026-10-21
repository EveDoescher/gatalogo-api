"""Reference jobs survive restart, isolate GPU work and discard obsolete results."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.persistence_models import User, RemoteCat, CatPhoto, VisionJob, SyncChange
from app.services import reference_analysis as worker
from app.services.vision_jobs import enqueue_recognition, enqueue_reference_analysis


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def queue(request, tmp_path, monkeypatch):
    schema = None
    if request.param == "postgres":
        url = os.environ.get("RECOGNITION_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires disposable PostgreSQL")
        if not url.endswith("/recognition_test"):
            raise ValueError("Tests require recognition_test")
        schema = "reference_" + uuid4().hex
        admin = create_async_engine(url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
    else:
        engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(tmp_path / "queue.db")
        )
    try:
        async with engine.begin() as connection:
            for model in (User, RemoteCat, CatPhoto, VisionJob, SyncChange):
                await connection.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        data = b"reference-photo-bytes"
        path = tmp_path / "photo.jpg"
        path.write_bytes(data)
        monkeypatch.setattr(worker.photo_storage, "open", lambda key: path)
        for name in (
            "account",
            "confirm_photo",
            "rebuild_analysis_from_photos",
            "publish_committed_events",
        ):
            monkeypatch.setattr(worker, name, AsyncMock())
        async with factory() as session:
            user = User(id=uuid4(), email=f"{uuid4()}@test.local")
            cat = RemoteCat(
                id=uuid4(),
                user_id=user.id,
                client_id=uuid4(),
                payload={},
                client_updated_at=datetime.now(UTC),
                updated_by_device="test",
            )
            photo = CatPhoto(
                id=uuid4(),
                cat_id=cat.id,
                kind="front",
                storage_key="photo.jpg",
                content_hash=hashlib.sha256(data).hexdigest(),
                status="analyzing",
            )
            session.add(user)
            await session.flush()
            session.add(cat)
            await session.flush()
            session.add(photo)
            await session.flush()
            gpu = await enqueue_recognition(session, cat=cat, photo=photo)
            job = await enqueue_reference_analysis(session, cat=cat, photo=photo)
            duplicate = await enqueue_reference_analysis(session, cat=cat, photo=photo)
            assert duplicate.id == job.id and gpu.id != job.id
            await session.commit()
        yield SimpleNamespace(
            factory=factory,
            job_id=job.id,
            gpu_id=gpu.id,
            photo_id=photo.id,
            cat_id=cat.id,
            backend=request.param,
            settings=SimpleNamespace(
                max_image_bytes=1024,
                vision_job_lease_seconds=30,
                vision_job_max_attempts=2,
            ),
        )
    finally:
        await engine.dispose()
        if schema:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


@pytest.mark.asyncio
async def test_committed_job_runs_from_new_consumer_and_keeps_gpu_pending(queue):
    analyzer = AsyncMock(return_value={"coat_type": "Rajado"})
    assert await worker.process_next(
        queue.factory, settings=queue.settings, analyzer=analyzer
    )
    assert not await worker.process_next(
        queue.factory, settings=queue.settings, analyzer=analyzer
    )
    analyzer.assert_awaited_once()
    worker.confirm_photo.assert_awaited_once()
    async with queue.factory() as session:
        assert (await session.get(VisionJob, queue.job_id)).status == "completed"
        assert (await session.get(VisionJob, queue.gpu_id)).status == "pending"
        photo = await session.get(CatPhoto, queue.photo_id)
        assert photo.status == "ready" and photo.analysis == {"coat_type": "Rajado"}
        assert len((await session.scalars(select(SyncChange))).all()) == 1


@pytest.mark.asyncio
async def test_failures_retry_then_mark_only_current_photo_failed(queue):
    analyzer = AsyncMock(side_effect=ValueError("temporary analysis failure"))
    for expected in ("pending", "failed"):
        assert await worker.process_next(
            queue.factory, settings=queue.settings, analyzer=analyzer
        )
        async with queue.factory() as session:
            assert (await session.get(VisionJob, queue.job_id)).status == expected
    assert not await worker.process_next(
        queue.factory, settings=queue.settings, analyzer=analyzer
    )
    async with queue.factory() as session:
        assert (await session.get(CatPhoto, queue.photo_id)).status == "failed"
        assert (await session.get(VisionJob, queue.job_id)).attempts == 2
    worker.confirm_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_replacement_before_processing_never_invokes_analysis(queue):
    async with queue.factory() as session:
        photo = await session.get(CatPhoto, queue.photo_id)
        photo.content_hash = "replacement"
        await session.commit()
    analyzer = AsyncMock()
    await worker.process_next(queue.factory, settings=queue.settings, analyzer=analyzer)
    analyzer.assert_not_awaited()
    async with queue.factory() as session:
        assert (await session.get(VisionJob, queue.job_id)).status == "superseded"
        assert (await session.get(CatPhoto, queue.photo_id)).status == "analyzing"


@pytest.mark.asyncio
async def test_cancellation_rolls_back_claim_and_next_process_resumes(queue):
    entered = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        worker.process_next(queue.factory, settings=queue.settings, analyzer=blocked)
    )
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with queue.factory() as session:
        job = await session.get(VisionJob, queue.job_id)
        assert job.status == "pending" and job.attempts == 0
    assert await worker.process_next(
        queue.factory,
        settings=queue.settings,
        analyzer=AsyncMock(return_value={"coat_type": "Rajado"}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["postgres"], indirect=True)
async def test_postgres_claim_skips_busy_job_and_rereads_replaced_photo(queue):
    if queue.backend != "postgres":
        pytest.skip("Concurrent row locks require PostgreSQL")
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await release.wait()
        return {"old": "analysis"}

    task = asyncio.create_task(
        worker.process_next(queue.factory, settings=queue.settings, analyzer=blocked)
    )
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert not await worker.process_next(
            queue.factory, settings=queue.settings, analyzer=AsyncMock()
        )
        async with queue.factory() as session:
            photo = await session.get(CatPhoto, queue.photo_id)
            photo.content_hash = "replacement-during-inference"
            await session.commit()
        release.set()
        assert await task
        async with queue.factory() as session:
            assert (await session.get(VisionJob, queue.job_id)).status == "superseded"
            photo = await session.get(CatPhoto, queue.photo_id)
            assert photo.status == "analyzing" and photo.analysis is None
        worker.confirm_photo.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_progression_failure_rolls_back_photo_and_staged_events(queue):
    from app.services.realtime_events import queue_event

    async def fail_after_staging(session, cat, photo, incoming, **kwargs):
        queue_event(session, user_id=cat.user_id, event="new_notification", data={})
        raise ValueError("progression temporarily unavailable")

    worker.confirm_photo.side_effect = fail_after_staging
    await worker.process_next(
        queue.factory,
        settings=queue.settings,
        analyzer=AsyncMock(return_value={"coat_type": "Rajado"}),
    )
    async with queue.factory() as session:
        photo = await session.get(CatPhoto, queue.photo_id)
        assert photo.analysis is None and photo.status == "analyzing"
        assert (await session.get(VisionJob, queue.job_id)).status == "pending"
        assert not list((await session.scalars(select(SyncChange))).all())
    published_session = worker.publish_committed_events.await_args.args[0]
    assert not published_session.info


@pytest.mark.asyncio
async def test_gpu_worker_rejects_reference_analysis_job(queue, monkeypatch):
    from app.services import vision_worker

    monkeypatch.setattr(vision_worker, "get_session_factory", lambda: queue.factory)
    gpu = object.__new__(vision_worker.Sam2DinoWorker)
    await gpu.process(queue.job_id)
    async with queue.factory() as session:
        assert (await session.get(VisionJob, queue.job_id)).status == "pending"
        assert (await session.get(CatPhoto, queue.photo_id)).analysis is None
