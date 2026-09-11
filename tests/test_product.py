"""Product contracts exercised against disposable PostgreSQL, with no model calls."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select

from test_recognition_database import db, setup_pair, SETTINGS
from app.persistence_models import (AppNotification, EmailOtp, Friendship, MatchSuggestion,
    ProductState, User, UserSession, RemoteCat, CatPhoto, FeedItem)
from app.routers import product, vision, social, auth
from app.schemas import MissingReportCreate, MessageCreate
from app.schemas import SightingCreate
from app.services.auth_service import hash_otp, consume_otp, hash_refresh_token, password_hash
from app.services.recognition_service import sync_catalog_sighting


@pytest.mark.asyncio
async def test_only_owned_cats_create_one_active_report(db):
    cats, _, _ = await setup_pair(db)
    owner = await db.get(User, cats[0].user_id)
    request = MissingReportCreate(cat_client_id=cats[0].client_id, latitude=0, longitude=0, radius_meters=1000)
    with pytest.raises(HTTPException) as rejected:
        await vision.create_report(request, owner, db)
    assert rejected.value.status_code == 422
    cats[0].payload = {"is_owned": 1}
    first = await vision.create_report(request, owner, db)
    second = await vision.create_report(request, owner, db)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_owned_pet_does_not_become_street_sighting(db):
    user = User(email=f"{uuid4()}@test.local")
    db.add(user)
    await db.flush()
    cat = RemoteCat(user_id=user.id, client_id=uuid4(), client_updated_at=datetime.now(UTC),
        updated_by_device="test", payload={"is_owned": 1, "latitude": 0, "longitude": 0})
    db.add(cat)
    await db.flush()
    await sync_catalog_sighting(db, cat=cat, settings=SETTINGS)
    from app.persistence_models import Sighting
    assert not (await db.execute(select(Sighting).where(Sighting.cat_id == cat.id))).scalars().all()


@pytest.mark.asyncio
async def test_offline_sighting_retries_keep_original_time_and_do_not_duplicate(db):
    cats, _, _ = await setup_pair(db)
    observer = await db.get(User, cats[1].user_id)
    when = datetime.now(UTC) - timedelta(days=2)
    request = SightingCreate(cat_client_id=cats[1].client_id, latitude=0, longitude=0, observed_at=when, note="Na praça")
    first = await vision.create_sighting(request, observer, db)
    second = await vision.create_sighting(request, observer, db)
    assert first["id"] == second["id"]
    from app.persistence_models import Sighting
    from uuid import UUID
    assert (await db.get(Sighting, UUID(first["id"]))).created_at == when


@pytest.mark.asyncio
async def test_edit_report_area_checks_owner_and_resolved_state(db):
    cats, report, _ = await setup_pair(db)
    owner, stranger = [await db.get(User, cat.user_id) for cat in cats]
    area = product.ReportArea(latitude=1, longitude=1, radius_meters=2000)
    with pytest.raises(HTTPException):
        await product.update_report_area(report.id, area, stranger, db)
    await product.update_report_area(report.id, area, owner, db)
    assert report.latitude == 1 and report.radius_meters == 2000
    await vision.resolve_report(report.id, owner, db)
    with pytest.raises(HTTPException):
        await product.update_report_area(report.id, area, owner, db)


@pytest.mark.asyncio
async def test_clues_keep_exact_location_and_scores_out_until_confirmed(db, monkeypatch, tmp_path):
    cats, report, sighting = await setup_pair(db)
    match = MatchSuggestion(missing_report_id=report.id, sighting_id=sighting.id, similarity=.93, status="pending", restricted=True)
    db.add(match)
    await db.flush()
    owner = await db.get(User, cats[0].user_id)
    stranger = await db.get(User, cats[1].user_id)
    rows = await product.clues(report.id, owner, db)
    assert rows[0]["latitude"] is None
    assert "similarity" not in rows[0] and "evidence" not in rows[0]
    with pytest.raises(HTTPException) as denied:
        await product.clue_photo(match.id, stranger, db)
    assert denied.value.status_code == 404
    path = tmp_path / "private.jpg"
    picture = Image.new("RGB", (32, 32), "white")
    exif = Image.Exif()
    exif[270] = "private metadata"
    picture.save(path, exif=exif)
    cats[1].photo_key = "private"
    monkeypatch.setattr(product.photo_storage, "open", lambda _: path)
    response = await product.clue_photo(match.id, owner, db)
    with Image.open(BytesIO(response.body)) as served:
        assert not served.getexif()
    await vision.decide_match(match.id, "confirm", owner, db)
    assert report.status == "active"
    assert (await product.clues(report.id, owner, db))[0]["latitude"] == sighting.latitude


@pytest.mark.asyncio
async def test_conversations_require_friendship_and_notify_only_recipient(db):
    cats, _, _ = await setup_pair(db)
    a, b = [await db.get(User, cat.user_id) for cat in cats]
    with pytest.raises(HTTPException):
        await product.start_conversation(b.id, a, db)
    db.add_all([Friendship(user_id=a.id, friend_id=b.id), Friendship(user_id=b.id, friend_id=a.id)])
    await db.flush()
    conversation = await product.start_conversation(b.id, a, db)
    assert (await product.start_conversation(b.id, a, db))["id"] == conversation["id"]
    await social.send_message(conversation["id"], MessageCreate(body="Vi um gato na praça."), a, db)
    notice = (await db.execute(select(AppNotification).where(AppNotification.type == "message"))).scalar_one()
    assert notice.user_id == b.id
    with pytest.raises(HTTPException):
        await product.read_notification(notice.id, a, db)
    await product.read_notification(notice.id, b, db)
    assert notice.read_at is not None
    await social.remove_friend(b.id, a, db)
    with pytest.raises(HTTPException):
        await social.send_message(conversation["id"], MessageCreate(body="teste"), a, db)


@pytest.mark.asyncio
async def test_achievements_are_durable_private_and_shared_explicitly_once(db):
    cats, _, _ = await setup_pair(db)
    user = await db.get(User, cats[0].user_id)
    user.username = f"u{uuid4().hex[:12]}"
    first = await product.journey(user, db)
    assert next(item for item in first if item["id"] == "first")["unlocked_at"]
    assert not (await db.execute(select(FeedItem))).scalars().all()
    cats[0].deleted_at = datetime.now(UTC)
    second = await product.journey(user, db)
    assert next(item for item in second if item["id"] == "first")["unlocked_at"]
    await product.share_achievement("first", user, db)
    await product.share_achievement("first", user, db)
    assert len((await db.execute(select(FeedItem))).scalars().all()) == 1
    assert len((await db.execute(select(AppNotification).where(AppNotification.type == "achievement"))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_wrong_otp_attempts_persist_and_reset_grant_is_single_use(db, monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    user = User(email=f"{uuid4()}@example.org", status="active", password_hash=password_hash.hash("old-password-123"))
    db.add(user)
    await db.flush()
    otp = EmailOtp(user_id=user.id, email=user.email, purpose="reset_password",
        code_hash=hash_otp(code="123456", email=user.email, purpose="reset_password", settings=settings),
        expires_at=datetime.now(UTC) + timedelta(minutes=10))
    db.add(otp)
    await db.flush()
    for expected in (1, 2):
        with pytest.raises(HTTPException):
            await consume_otp(db, email=user.email, purpose="reset_password", code="000000")
        await db.refresh(otp)
        assert otp.attempts == expected
    tokens = await auth.issue_tokens(db, user=user, device_id="test")
    grant = await auth.verify_reset_code(auth.ResetCodeRequest(email=user.email, code="123456"), db)
    request = auth.NewPasswordRequest(reset_token=grant["reset_token"], password="new-password-123")
    await auth.complete_reset(request, db)
    assert password_hash.verify("new-password-123", user.password_hash)
    sessions = (await db.execute(select(UserSession).where(UserSession.user_id == user.id))).scalars().all()
    assert all(item.revoked_at is not None for item in sessions)
    with pytest.raises(HTTPException):
        await auth.complete_reset(request, db)
