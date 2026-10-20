from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.persistence_models import MissingReport, User
from app.schemas import MissingPosterUpdate, PosterContact, PosterOptions
from app.routers import vision, social
from app.services.missing_posters import public_poster_details
from test_progression import account, saved_cat


@pytest.mark.asyncio
async def test_poster_choices_persist_and_public_projection_excludes_hidden_data(account):
    db, owner = account
    owner.username = "owner"
    cat = await saved_cat(db, owner)
    cat.payload = {**cat.payload, "microchip": "private-chip", "collar": "Azul"}
    report = MissingReport(user_id=owner.id, cat_id=cat.id, latitude=0, longitude=0, radius_meters=100,
                           poster_details={"last_seen_address": "private-address"})
    db.add(report); await db.flush()
    changed = await vision.update_poster(report.id, MissingPosterUpdate(
        options=PosterOptions(address=False, microchip=False, qr_code=False),
        contacts=[PosterContact(phone="11999991234", whatsapp=True), PosterContact(phone="1133331234", included=False)]), owner, db)
    await db.refresh(report)
    assert len(changed.poster_contacts) == 2
    assert len(changed.contacts) == 1
    public = public_poster_details(report, cat)
    assert public["microchip"] is None and public["last_seen_address"] is None
    assert public["contacts"] == [{"phone": "11999991234", "whatsapp": True}]
    case = await social.community_report(report.id, owner, db)
    assert "poster_contacts" not in case
    assert case["microchip"] is None
    stranger = User(email=f"{uuid4()}@test.local", username="stranger")
    db.add(stranger); await db.flush()
    with pytest.raises(HTTPException) as exc:
        await vision.update_poster(report.id, MissingPosterUpdate(), stranger, db)
    assert exc.value.status_code == 404
    report.status = "resolved"; await db.flush()
    assert public_poster_details(report, cat)["contacts"] == []
    with pytest.raises(HTTPException) as exc:
        await vision.update_poster(report.id, MissingPosterUpdate(), owner, db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_owner_publication_is_linked_idempotent_and_stops_after_resolution(account):
    db, owner = account
    owner.username = "owner"
    cat = await saved_cat(db, owner)
    report = MissingReport(user_id=owner.id, cat_id=cat.id, latitude=0, longitude=0, radius_meters=100)
    db.add(report); await db.flush()
    first = await social.publish_report(report.id, owner, db)
    second = await social.publish_report(report.id, owner, db)
    assert first.id == second.id
    assert first.type == "poster_repost" and first.payload["report_id"] == str(report.id)
    report.status = "resolved"; await db.flush()
    with pytest.raises(HTTPException):
        await social.publish_report(report.id, owner, db)


@pytest.mark.asyncio
async def test_public_qr_page_redacts_hidden_data_and_revokes_photo_after_closure(account):
    from app.routers.public_search import public_search, public_photo
    db, owner = account
    cat = await saved_cat(db, owner)
    cat.payload = {**cat.payload, "name": "<script>bad()</script>", "microchip": "private-chip"}
    report = MissingReport(user_id=owner.id, cat_id=cat.id, latitude=0, longitude=0, radius_meters=100,
        poster_details={"last_seen_address": "secret-address", "poster_options": {"address": False, "microchip": False},
            "poster_contacts": [{"phone": "1133331234", "included": False}]})
    db.add(report); await db.flush()
    response = await public_search(report.id, db)
    html = response.body.decode()
    assert "secret-address" not in html and "private-chip" not in html and "1133331234" not in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert response.headers["cache-control"] == "no-store"
    report.poster_details = {"poster_options": {"qr_code": False}}
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await public_search(report.id, db)
    assert exc.value.status_code == 404
    report.status = "resolved"; await db.flush()
    html = (await public_search(report.id, db)).body.decode()
    assert "Busca encerrada" in html and "<img" not in html and "<script>" not in html
    with pytest.raises(HTTPException):
        await public_photo(report.id, db)

# Imported pytest fixtures are discovered through module globals.
__all__ = ['account']
