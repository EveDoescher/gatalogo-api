"""Real database equivalence and bounded SELECT counts for catalog pages."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.persistence_models import CatPhoto, RemoteCat, SyncChange, User
from app.routers.sync import pull
from app.schemas import ReferencePhotoResponse, SyncCatRecord, SyncPullResponse


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def catalog(request):
    schema, admin = None, None
    if request.param == "postgres":
        url = os.environ.get("RECOGNITION_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires disposable PostgreSQL")
        if not url.endswith("/recognition_test"):
            raise ValueError("Only the disposable recognition_test database is allowed")
        schema = "sync_perf_" + uuid4().hex
        admin = create_async_engine(url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema}}
        )
    else:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            for model in (User, RemoteCat, CatPhoto, SyncChange):
                await connection.run_sync(model.__table__.create)
        yield SimpleNamespace(
            engine=engine, factory=async_sessionmaker(engine, expire_on_commit=False)
        )
    finally:
        await engine.dispose()
        if schema:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


async def seed(factory, count):
    now = datetime.now(UTC)
    async with factory() as session:
        user, other = (
            User(email=f"{uuid4()}@test.local"),
            User(email=f"{uuid4()}@test.local"),
        )
        session.add_all([user, other])
        await session.flush()
        cats = []
        for index in range(count):
            cat = RemoteCat(
                user_id=user.id,
                client_id=uuid4(),
                client_updated_at=now,
                updated_by_device="device",
                payload={"name": f"Gato {index}", "unknown": [1, 2]},
                analysis={"coat_type": "Rajado"},
                photo_key="private.jpg",
                deleted_at=now if index == count - 1 and count > 1 else None,
            )
            session.add(cat)
            await session.flush()
            cats.append(cat)
            session.add(SyncChange(user_id=user.id, cat_id=cat.id))
            for kind in ("primary", "front", "left", "back"):
                session.add(
                    CatPhoto(
                        cat_id=cat.id,
                        kind=kind,
                        storage_key=kind,
                        content_hash=kind,
                        status="analyzing" if kind == "left" else "ready",
                        deleted_at=now if kind == "back" else None,
                    )
                )
        await session.flush()
        # A revision beyond the first page must still be presented as the latest,
        # while the cursor remains tied to the changes actually consumed.
        session.add(SyncChange(user_id=user.id, cat_id=cats[0].id))
        foreign = RemoteCat(
            user_id=other.id,
            client_id=uuid4(),
            client_updated_at=now,
            updated_by_device="other",
            payload={},
        )
        session.add(foreign)
        await session.flush()
        session.add(SyncChange(user_id=other.id, cat_id=foreign.id))
        await session.commit()
        return user, cats


async def legacy_pull(session, user, cursor, limit):
    changes = list(
        await session.scalars(
            select(SyncChange)
            .where(SyncChange.user_id == user.id, SyncChange.revision > cursor)
            .order_by(SyncChange.revision)
            .limit(limit)
        )
    )
    latest = {change.cat_id: change for change in changes}
    records = []
    for change in latest.values():
        cat = await session.get(RemoteCat, change.cat_id)
        revision = await session.scalar(
            select(SyncChange.revision)
            .where(SyncChange.cat_id == cat.id)
            .order_by(SyncChange.revision.desc())
            .limit(1)
        )
        photos = list(
            await session.scalars(
                select(CatPhoto).where(
                    CatPhoto.cat_id == cat.id,
                    CatPhoto.kind.in_(("front", "left", "right", "back")),
                    CatPhoto.deleted_at.is_(None),
                )
            )
        )
        records.append(
            SyncCatRecord(
                client_id=cat.client_id,
                updated_at=cat.client_updated_at,
                device_id=cat.updated_by_device,
                deleted_at=cat.deleted_at,
                payload=cat.payload or {},
                analysis=cat.analysis,
                photo_available=bool(cat.photo_key and cat.deleted_at is None),
                photo_url=f"/sync/cats/{cat.client_id}/photo"
                if cat.photo_key and cat.deleted_at is None
                else None,
                reference_photos=[
                    ReferencePhotoResponse(
                        kind=photo.kind,
                        status=photo.status,
                        photo_url=f"/sync/cats/{cat.client_id}/reference-photos/{photo.kind}",
                        updated_at=photo.updated_at,
                    )
                    for photo in photos
                ],
                avatar=None,
                revision=revision or 0,
            )
        )
    return SyncPullResponse(
        changes=records, next_cursor=changes[-1].revision if changes else cursor
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 40])
async def test_page_matches_legacy_response_with_four_selects(catalog, count):
    user, cats = await seed(catalog.factory, count)
    statements = []

    def collect(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(catalog.engine.sync_engine, "before_cursor_execute", collect)
    try:
        async with catalog.factory() as session:
            expected = await legacy_pull(session, user, 0, count)
            old_count = len(statements)
            session.expunge_all()
            statements.clear()
            actual = await pull(cursor=0, limit=count, user=user, session=session)
            assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
            assert len(statements) == 4
            assert old_count == 1 + 3 * count
            assert [row.client_id for row in actual.changes] == [
                cat.client_id for cat in cats
            ]
            assert actual.changes[0].revision > actual.next_cursor
            if count > 1:
                assert actual.changes[-1].photo_url is None
            assert {p.kind for p in actual.changes[0].reference_photos} == {
                "front",
                "left",
            }
            following = await pull(
                cursor=actual.next_cursor, limit=count, user=user, session=session
            )
            assert [row.client_id for row in following.changes] == [cats[0].client_id]
            statements.clear()
            empty = await pull(
                cursor=following.next_cursor, limit=count, user=user, session=session
            )
            assert empty.changes == [] and empty.next_cursor == following.next_cursor
            assert len(statements) == 1
    finally:
        event.remove(catalog.engine.sync_engine, "before_cursor_execute", collect)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "missing-key", "rejected"])
async def test_primary_analysis_releases_authentication_connection(
    catalog, monkeypatch, outcome
):
    from io import BytesIO
    from unittest.mock import AsyncMock
    from fastapi import HTTPException, UploadFile
    from app import main
    from app.exceptions import MissingApiKeyError, RejectedImageError
    from app.models.analysis import CatAnalysisResponse

    user, cats = await seed(catalog.factory, 1)
    user_id, client_id = user.id, cats[0].client_id
    result = CatAnalysisResponse(
        cat_id=str(client_id),
        coat_type="Rajado",
        primary_color="Preto",
        colors=[],
        confidence=0.9,
        pattern_map={"base_color": "Preto", "pattern": "Rajado"},
        analyzed_at=datetime.now(UTC),
    )
    active = 0

    def checkout(*args):
        nonlocal active
        active += 1

    def checkin(*args):
        nonlocal active
        active -= 1

    event.listen(catalog.engine.sync_engine, "checkout", checkout)
    event.listen(catalog.engine.sync_engine, "checkin", checkin)

    class Analyzer:
        def __init__(self, settings):
            pass

        async def analyze(self, **kwargs):
            assert active == 0, "Authentication connection retained during inference"
            assert kwargs == {
                "cat_id": str(client_id),
                "image_bytes": b"captured-photo",
            }
            if outcome == "missing-key":
                raise MissingApiKeyError("missing test key")
            if outcome == "rejected":
                raise RejectedImageError("NOT_A_CAT", "Não é um gato.")
            return result

    monkeypatch.setattr(main, "AnalysisService", Analyzer)
    for name in (
        "progress_account",
        "confirm_photo",
        "enqueue_recognition",
        "publish_committed_events",
        "reject_photo",
    ):
        monkeypatch.setattr(main, name, AsyncMock())
    monkeypatch.setattr(
        main.photo_storage, "save", lambda **kwargs: ("same-photo.jpg", "a" * 64)
    )
    try:
        async with catalog.factory() as session:
            authenticated = await session.get(User, user_id)
            assert active == 1
            image = UploadFile(file=BytesIO(b"captured-photo"), filename="cat.jpg")
            if outcome == "success":
                actual = await main.analyze_cat(
                    cat_id=str(client_id),
                    image=image,
                    user=authenticated,
                    session=session,
                )
                assert actual.model_dump(mode="json") == result.model_dump(mode="json")
                cat = await session.get(RemoteCat, cats[0].id)
                assert cat.analysis == result.model_dump(mode="json")
                assert cat.payload["status"] == "completed"
                main.confirm_photo.assert_awaited_once()
                main.enqueue_recognition.assert_awaited_once()
                main.publish_committed_events.assert_awaited_once()
            else:
                with pytest.raises(HTTPException) as failure:
                    await main.analyze_cat(
                        cat_id=str(client_id),
                        image=image,
                        user=authenticated,
                        session=session,
                    )
                assert failure.value.status_code == (
                    503 if outcome == "missing-key" else 422
                )
                assert failure.value.detail == {
                    "code": "MISSING_API_KEY"
                    if outcome == "missing-key"
                    else "NOT_A_CAT",
                    "message": "missing test key"
                    if outcome == "missing-key"
                    else "Não é um gato.",
                }
                main.confirm_photo.assert_not_awaited()
                main.publish_committed_events.assert_not_awaited()
                if outcome == "rejected":
                    main.reject_photo.assert_awaited_once_with(
                        session,
                        user_id,
                        str(client_id),
                        b"captured-photo",
                        "Não é um gato.",
                    )
    finally:
        event.remove(catalog.engine.sync_engine, "checkout", checkout)
        event.remove(catalog.engine.sync_engine, "checkin", checkin)
