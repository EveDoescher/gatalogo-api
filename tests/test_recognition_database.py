"""Real PostgreSQL tests, opt-in against a dedicated disposable database."""
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.persistence_models import (AppNotification, CatPhoto, CatVisualEmbedding,
    MatchSuggestion, MissingReport, RemoteCat, Sighting, User)
from app.services.recognition_features import model_version
from app.services.recognition_service import (create_report_matches,
    create_sighting_matches, refresh_cat_matches, sync_catalog_sighting)

SETTINGS = Settings(_env_file=None, match_shadow_mode=False)


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("RECOGNITION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a disposable PostgreSQL database with migrations applied")
    if not url.endswith("/recognition_test"):
        raise ValueError("Tests require a database named recognition_test")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


async def setup_pair(db, *, solid=False, distance=0, target_embeddings=True):
    owner, observer = User(email=f"{uuid4()}@test.local"), User(email=f"{uuid4()}@test.local")
    db.add_all([owner, observer])
    await db.flush()
    cats = []
    for user in (owner, observer):
        cat = RemoteCat(user_id=user.id, client_id=uuid4(), client_updated_at=datetime.now(UTC),
            updated_by_device="test", payload={"latitude": 0, "longitude": 0},
            analysis={"coat_type": "Sólido" if solid else "Rajado"})
        db.add(cat)
        await db.flush()
        cats.append(cat)
    report = MissingReport(user_id=owner.id, cat_id=cats[0].id, latitude=0, longitude=0, radius_meters=1000)
    sighting = Sighting(observer_id=observer.id, cat_id=cats[1].id, latitude=distance, longitude=0)
    db.add_all([report, sighting])
    await db.flush()
    for cat in cats if target_embeddings else cats[1:]:
        await add_embeddings(db, cat)
    return cats, report, sighting


async def add_embeddings(db, cat):
    for kind in ("primary", "front"):
        photo = CatPhoto(cat_id=cat.id, kind=kind, storage_key="unused-test-photo", content_hash=uuid4().hex)
        db.add(photo)
        await db.flush()
        db.add(CatVisualEmbedding(user_id=cat.user_id, cat_id=cat.id, photo_id=photo.id,
            photo_hash=photo.content_hash, model_version=model_version(SETTINGS),
            embedding=[1.0]+[0.0]*383, quality_score=0.9, view="unknown", region="body",
            is_solid_coat=False, features={}))
    await db.flush()


@pytest.mark.asyncio
async def test_radius_and_repeat_do_not_duplicate_notifications(db):
    cats, report, sighting = await setup_pair(db, distance=0.02)
    assert await create_sighting_matches(db, sighting=sighting, settings=SETTINGS) == 0
    sighting.latitude = 0.001
    await db.flush()
    assert await create_sighting_matches(db, sighting=sighting, settings=SETTINGS) == 1
    assert await create_sighting_matches(db, sighting=sighting, settings=SETTINGS) == 0
    match = (await db.execute(select(MatchSuggestion))).scalar_one()
    notification = (await db.execute(select(AppNotification))).scalar_one()
    assert notification.payload["match_id"] == str(match.id)
    assert not match.restricted


@pytest.mark.asyncio
async def test_target_processed_after_sighting_runs_reverse_search(db):
    cats, report, sighting = await setup_pair(db, target_embeddings=False)
    assert await create_report_matches(db, report=report, settings=SETTINGS) == 0
    await add_embeddings(db, cats[0])
    await refresh_cat_matches(db, cat=cats[0], settings=SETTINGS)
    assert (await db.execute(select(MatchSuggestion))).scalar_one()


@pytest.mark.asyncio
async def test_solid_candidates_stay_private(db):
    _, report, sighting = await setup_pair(db, solid=True)
    await create_report_matches(db, report=report, settings=SETTINGS)
    match = (await db.execute(select(MatchSuggestion))).scalar_one()
    assert match.restricted
    assert not (await db.execute(select(AppNotification))).scalars().all()


@pytest.mark.asyncio
async def test_replaced_and_deleted_photos_cannot_match(db):
    cats, report, sighting = await setup_pair(db)
    await create_report_matches(db, report=report, settings=SETTINGS)
    photos = (await db.execute(select(CatPhoto).where(CatPhoto.cat_id == cats[0].id))).scalars().all()
    photos[0].content_hash = "replacement"
    photos[1].deleted_at = datetime.now(UTC)
    await db.flush()
    await refresh_cat_matches(db, cat=cats[0], settings=SETTINGS)
    match = (await db.execute(select(MatchSuggestion))).scalar_one()
    assert match.status == "stale"


@pytest.mark.asyncio
async def test_deleted_cats_and_resolved_reports_are_excluded(db):
    cats, report, sighting = await setup_pair(db)
    cats[1].deleted_at = datetime.now(UTC)
    await db.flush()
    assert await create_sighting_matches(db, sighting=sighting, settings=SETTINGS) == 0
    cats[1].deleted_at = None
    report.status = "resolved"
    await db.flush()
    assert await create_sighting_matches(db, sighting=sighting, settings=SETTINGS) == 0


@pytest.mark.asyncio
async def test_catalog_coordinates_generate_one_observation(db):
    cats, _, _ = await setup_pair(db)
    for _ in range(2):
        await sync_catalog_sighting(db, cat=cats[1], settings=SETTINGS)
    sightings = (await db.execute(select(Sighting).where(Sighting.cat_id == cats[1].id))).scalars().all()
    assert len(sightings) == 1


@pytest.mark.asyncio
async def test_jobs_are_bound_to_photo_content(db):
    from app.services.vision_jobs import enqueue_recognition
    cats, _, _ = await setup_pair(db)
    photo = (await db.execute(select(CatPhoto).where(CatPhoto.cat_id == cats[0].id).limit(1))).scalar_one()
    first = await enqueue_recognition(db, cat=cats[0], photo=photo)
    again = await enqueue_recognition(db, cat=cats[0], photo=photo)
    assert first.id == again.id
    photo.content_hash = uuid4().hex
    await db.flush()
    replacement = await enqueue_recognition(db, cat=cats[0], photo=photo)
    assert first.id != replacement.id


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_fails", [False, True])
async def test_worker_commits_evidence_atomically(db, tmp_path, monkeypatch, matching_fails):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.persistence_models import VisionJob
    from app.services import vision_worker
    from app.services.storage_service import PrivatePhotoStorage
    from app.services.vision_jobs import enqueue_recognition
    cats, _, _ = await setup_pair(db)
    photo = (await db.execute(select(CatPhoto).where(
        CatPhoto.cat_id == cats[0].id, CatPhoto.kind == "primary"))).scalar_one()
    storage = PrivatePhotoStorage(tmp_path)
    photo.storage_key, photo.content_hash = storage.save(
        user_id=cats[0].user_id, cat_id=cats[0].id, data=b"test-bytes", suffix=".jpg")
    old = (await db.execute(select(CatVisualEmbedding).where(CatVisualEmbedding.photo_id == photo.id))).scalar_one()
    old.photo_hash = photo.content_hash
    old.quality_score = 0.6
    job = await enqueue_recognition(db, cat=cats[0], photo=photo)
    await db.flush()
    factory = async_sessionmaker(db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(vision_worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(vision_worker, "photo_storage", storage)
    worker = vision_worker.Sam2DinoWorker(SETTINGS)
    monkeypatch.setattr(worker, "extract_bytes", lambda data: {
        "embedding": [1.0]+[0.0]*383, "quality_score": 0.9,
        "features": {}, "model_version": model_version(SETTINGS)})
    if matching_fails:
        async def fail(*args, **kwargs):
            raise RuntimeError("simulated matching failure")
        monkeypatch.setattr(vision_worker, "refresh_cat_matches", fail)
    await worker.process(job.id)
    await db.refresh(job)
    # Force fresh reads, since the worker uses its own ORM identity map.
    records = (await db.execute(select(CatVisualEmbedding).where(
        CatVisualEmbedding.photo_id == photo.id).execution_options(populate_existing=True))).scalars().all()
    assert len(records) == 1
    if matching_fails:
        assert job.status == "pending" and job.attempts == 1
        assert records[0].quality_score == 0.6
    else:
        assert job.status == "completed"
        assert records[0].quality_score == 0.9


@pytest.mark.asyncio
async def test_superseded_job_cannot_process_old_photo(db, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import vision_worker
    from app.services.vision_jobs import enqueue_recognition
    cats, _, _ = await setup_pair(db)
    photo = (await db.execute(select(CatPhoto).where(CatPhoto.cat_id == cats[0].id).limit(1))).scalar_one()
    job = await enqueue_recognition(db, cat=cats[0], photo=photo)
    photo.content_hash = uuid4().hex
    await db.flush()
    factory = async_sessionmaker(db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(vision_worker, "get_session_factory", lambda: factory)
    worker = vision_worker.Sam2DinoWorker(SETTINGS)
    await worker.process(job.id)
    await db.refresh(job)
    assert job.status == "superseded"


@pytest.mark.asyncio
async def test_shadow_records_decision_without_notification_and_can_transition(db):
    _, report, _ = await setup_pair(db)
    await create_report_matches(db, report=report, settings=Settings(_env_file=None))
    match = (await db.execute(select(MatchSuggestion))).scalar_one()
    assert not match.restricted
    assert match.evidence["would_notify"]
    assert not match.evidence["notification_eligible"]
    assert match.evidence["probability"] is None
    assert not (await db.execute(select(AppNotification))).scalars().all()
    # Explicitly enabling the live policy can notify once on reevaluation.
    await create_report_matches(db, report=report, settings=SETTINGS)
    await create_report_matches(db, report=report, settings=SETTINGS)
    assert len((await db.execute(select(AppNotification))).scalars().all()) == 1
