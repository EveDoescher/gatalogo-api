"""Audience and revocable public-link probes on disposable storage/database."""

from io import BytesIO

from PIL import Image
import pytest

pytest.register_assert_rewrite("security_cases", "remaining_mechanism_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from remaining_mechanism_cases import seed_cat
from app.persistence_models import FeedItem, Friendship, MissingReport, RemoteCat, User


def photo():
    output = BytesIO()
    metadata = Image.Exif()
    metadata[270] = "PRIVATE_EXIF_AUDIENCE_CANARY"
    metadata[274] = 6
    Image.new("RGB", (128, 256), "white").save(output, format="JPEG", exif=metadata)
    return output.getvalue()


def sanitized(response):
    assert response.status_code == 200
    assert b"PRIVATE_EXIF_AUDIENCE_CANARY" not in response.content
    with Image.open(BytesIO(response.content)) as image:
        assert not image.getexif()
        assert image.size == (256, 128)


@pytest.mark.asyncio
@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("viewer", ["A", "B", "C"])
async def test_SEC04_feed_json_and_link_share_same_audience(db, public, viewer):
    filename = str(uid("audience-post-photo")) + ".jpg"
    target = db.storage.absolute_path(
        "posts/" + str(uid("audience-post-photo")) + "-synthetic.jpg"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(photo())
    url = "/social/posts/photo/" + filename
    async with db.factory() as session:
        owner = await session.get(User, uid("B"))
        owner.username = "synthetic_b"
        session.add(Friendship(user_id=uid("A"), friend_id=uid("B")))
        session.add(
            FeedItem(
                id=uid("audience-post"),
                user_id=uid("B"),
                type="cat_post",
                payload={
                    "is_public": public,
                    "text": "AUDIENCE_POST_CANARY",
                    "photo_url": url,
                },
                created_at=NOW,
            )
        )
        await session.commit()
    allowed = public or viewer in ("A", "B")
    listing = await db.client.get("/social/feed", headers=db.headers(viewer))
    assert listing.status_code == 200
    assert ("AUDIENCE_POST_CANARY" in listing.text) is allowed
    response = await db.client.get(url, headers=db.headers(viewer))
    assert response.status_code == (200 if allowed else 404)
    if allowed:
        sanitized(response)
        assert response.headers["cache-control"] == "private, no-store"
    assert (await db.client.get(url)).status_code == 401
    evidence(
        f"SEC04-feed-link-{public}-{viewer}",
        {
            "public": public,
            "viewer": viewer,
            "json_visible": allowed,
            "media_status": response.status_code,
            "anonymous_media": 401,
            "exif_removed_on_allowed_media": allowed,
        },
    )


@pytest.mark.asyncio
async def test_SEC04_gallery_strips_metadata_and_rotation(db):
    await seed_cat(db)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("mechanism-cat-B"))
        db.storage.open(cat.photo_key).write_bytes(photo())
    response = await db.client.get(
        f"/social/gallery/{uid('mechanism-cat-B')}/photo", headers=db.headers("B")
    )
    sanitized(response)
    assert response.headers["cache-control"] == "private, no-store"
    evidence(
        "SEC04-gallery-metadata",
        {
            "exif_removed": True,
            "orientation_applied": True,
            "cache": "private, no-store",
        },
    )


@pytest.mark.asyncio
async def test_SEC04_public_search_revocation_and_resolved_media(db):
    await seed_cat(db)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("mechanism-cat-B"))
        db.storage.open(cat.photo_key).write_bytes(photo())
        session.add(
            MissingReport(
                id=uid("audience-report"),
                user_id=uid("B"),
                cat_id=cat.id,
                latitude=-23.123456,
                longitude=-46.123456,
                radius_meters=100,
                status="active",
                poster_details={
                    "last_seen_address": "PRIVATE_ADDRESS_CANARY",
                    "microchip": "PRIVATE_CHIP_CANARY",
                    "poster_contacts": [
                        {"phone": "PRIVATE_CONTACT_CANARY", "included": False}
                    ],
                    "poster_options": {
                        "address": False,
                        "microchip": False,
                        "qr_code": True,
                    },
                },
            )
        )
        await session.commit()
    base = f"/public/busca/{uid('audience-report')}"
    page = await db.client.get(base)
    assert page.status_code == 200 and "<img" in page.text
    for canary in (
        "PRIVATE_ADDRESS_CANARY",
        "PRIVATE_CHIP_CANARY",
        "PRIVATE_CONTACT_CANARY",
        "-23.123456",
        "-46.123456",
    ):
        assert canary not in page.text
    assert page.headers["cache-control"] == "no-store"
    sanitized(await db.client.get(base + "/foto"))
    for actor in ("A", "B", "C"):
        community = await db.client.get(
            f"/social/reports/{uid('audience-report')}", headers=db.headers(actor)
        )
        assert community.status_code == 200
        assert "PRIVATE_" not in community.text
    async with db.factory() as session:
        report = await session.get(MissingReport, uid("audience-report"))
        report.poster_details = {
            **report.poster_details,
            "poster_options": {"qr_code": False, "address": False, "microchip": False},
        }
        await session.commit()
    assert (await db.client.get(base)).status_code == 404
    assert (await db.client.get(base + "/foto")).status_code == 404
    async with db.factory() as session:
        report = await session.get(MissingReport, uid("audience-report"))
        report.status = "resolved"
        await session.commit()
    closed = await db.client.get(base)
    assert closed.status_code == 200 and "Busca encerrada" in closed.text
    assert "<img" not in closed.text and "PRIVATE_" not in closed.text
    assert (await db.client.get(base + "/foto")).status_code == 404
    for actor in ("A", "B", "C"):
        details = await db.client.get(
            f"/social/reports/{uid('audience-report')}", headers=db.headers(actor)
        )
        assert details.status_code == 200 and details.json()["photo_url"] is None
        listing = await db.client.get("/social/reports", headers=db.headers(actor))
        assert str(uid("audience-report")) not in listing.text
    evidence(
        "SEC04-public-link-lifecycle",
        {
            "active_anonymous_page_and_photo": 200,
            "private_canaries_absent": True,
            "qr_disabled_page_and_photo": 404,
            "resolved_page": 200,
            "resolved_photo": 404,
            "resolved_absent_from_community_list": True,
            "actors": ["owner", "friend", "stranger", "anonymous-public-link"],
        },
    )
