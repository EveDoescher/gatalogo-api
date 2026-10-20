from datetime import UTC, datetime
from uuid import uuid4
from app.routers.social import CatPostCreate, FeedItemResponse
from app.services.storage_service import photo_storage


def test_cat_post_create_schema():
    # Schema supports photo_url and is_public
    post = CatPostCreate(
        cat_name="Frajola",
        note="Avistado na praça",
        location_name="Curitiba, PR",
        photo_url="/social/posts/photo/123.jpg",
        is_public=True,
    )
    assert post.cat_name == "Frajola"
    assert post.photo_url == "/social/posts/photo/123.jpg"
    assert post.is_public is True
    assert post.photo_base64 is None


def test_text_post_does_not_require_a_cat_or_photo():
    post = CatPostCreate(note="Uma conversa sobre nossos gatinhos.")
    assert post.cat_name == "" and post.photo_url is None


def test_post_photo_storage():
    storage = photo_storage
    post_id = uuid4()
    sample_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb"

    key, content_hash = storage.save_post_photo(
        post_id=post_id,
        data=sample_bytes,
        suffix=".jpg",
    )

    assert key.startswith("posts/")
    assert content_hash
    path = storage.open(key)
    assert path.exists()
    assert path.read_bytes() == sample_bytes

    # Clean up
    storage.delete(key)
    assert not path.exists()


def test_feed_item_response_schema():
    item_id = uuid4()
    now = datetime.now(UTC)
    resp = FeedItemResponse(
        id=item_id,
        username="gatalogo_oficial",
        type="tip",
        payload={
            "title": "Boas-vindas ao Gatálogo!",
            "note": "Fotografe qualquer gato para começar seu catálogo.",
            "tip_type": "welcome",
            "action_text": "Fotografar Primeiro Gato",
            "likes_count": 10,
            "has_liked": False,
            "comments": [],
        },
        created_at=now,
    )
    assert resp.type == "tip"
    assert resp.username == "gatalogo_oficial"
    assert resp.payload["tip_type"] == "welcome"
    assert resp.payload["likes_count"] == 10
