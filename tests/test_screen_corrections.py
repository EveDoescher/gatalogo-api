from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from test_recognition_database import db, setup_pair
from app.persistence_models import FeedItem, MissingReport, User
from app.routers import product, social, vision
from app.schemas import MissingReportCreate, ReportContact


@pytest.mark.parametrize("phone", ["        ", "---()---", "1234", "+123456789012345678", "call me"])
def test_contact_rejects_invalid_phone(phone):
    with pytest.raises(ValidationError):
        ReportContact(phone=phone)


def test_contacts_are_optional_and_bounded():
    payload = dict(cat_client_id=uuid4(), latitude=0, longitude=0, radius_meters=1000)
    assert MissingReportCreate(**payload).contacts == []
    with pytest.raises(ValidationError):
        MissingReportCreate(**payload, contacts=[{"phone": "11999991234"}] * 6)


@pytest.mark.asyncio
async def test_report_poster_details_survive_reload_and_area_edit(db):
    cats, existing, _ = await setup_pair(db)
    existing.status = "resolved"
    cats[0].payload = {"is_owned": True, "collar": "Azul", "microchip": "123456789", "distinguishing_marks": "Pata branca"}
    owner = await db.get(User, cats[0].user_id)
    created = await vision.create_report(MissingReportCreate(
        cat_client_id=cats[0].client_id, latitude=0, longitude=0, radius_meters=1000,
        last_seen_address="Rua das Flores, 15, Centro",
        contacts=[ReportContact(phone="(11) 99999-1234", whatsapp=True), ReportContact(phone="(11) 3333-1234")],
    ), owner, db)
    row = await db.get(MissingReport, created.id)
    await db.refresh(row)
    assert row.poster_details["last_seen_address"] == "Rua das Flores, 15, Centro"
    reports = await product.reports(owner, db)
    poster = next(report for report in reports if report["id"] == created.id)
    assert poster["contacts"] == [{"phone": "(11) 99999-1234", "whatsapp": True}, {"phone": "(11) 3333-1234", "whatsapp": False}]
    assert poster["collar"] == "Azul"
    assert poster["microchip"] == "123456789"
    assert poster["distinguishing_marks"] == "Pata branca"
    await product.update_report_area(created.id, product.ReportArea(latitude=0.001, longitude=0, radius_meters=1000, last_seen_address="Rua Nova, 20"), owner, db)
    await db.refresh(row)
    assert row.poster_details["last_seen_address"] == "Rua Nova, 20"
    assert len(row.poster_details["contacts"]) == 2


@pytest.mark.asyncio
async def test_old_report_has_no_invented_address_or_contacts(db):
    cats, report, _ = await setup_pair(db)
    response = vision._report_response(report, cats[0])
    assert response.last_seen_address is None
    assert response.contacts == []


@pytest.mark.asyncio
async def test_empty_feed_does_not_create_official_posts(db):
    user = User(email=f"{uuid4()}@test.local", username=f"user_{uuid4().hex[:10]}")
    db.add(user)
    await db.flush()
    result = await social.feed(limit=20, cursor=None, mode="all", user=user, session=db)
    assert result == []
    assert await db.get(User, social.OFFICIAL_USER_ID) is None
    assert not (await db.execute(select(FeedItem))).scalars().all()


@pytest.mark.asyncio
async def test_feed_excludes_legacy_fake_posts_and_keeps_real_posts(db):
    official = User(id=social.OFFICIAL_USER_ID, email="oficial@test.local", username="gatalogo_oficial")
    user = User(email=f"{uuid4()}@test.local", username=f"user_{uuid4().hex[:10]}")
    db.add_all([official, user])
    await db.flush()
    db.add(FeedItem(user_id=official.id, type="tip", payload={"note": "fake"}))
    real = FeedItem(user_id=user.id, type="cat_post", payload={"cat_name": "Lua", "is_public": True})
    db.add(real)
    await db.flush()
    for mode in ["all", "friends"]:
        result = await social.feed(limit=20, cursor=None, mode=mode, user=user, session=db)
        assert [post.id for post in result] == [real.id]

# Imported pytest fixtures are discovered through module globals.
__all__ = ['db']
