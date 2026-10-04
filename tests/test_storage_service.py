import hashlib
from uuid import uuid4

from app.services.storage_service import PrivatePhotoStorage


def test_photo_variants_use_content_hash_and_stay_inside_storage_root(tmp_path):
    storage = PrivatePhotoStorage(tmp_path)
    user_id, cat_id, conversation_id, message_id, post_id = (uuid4() for _ in range(5))
    data = b"photo bytes"
    expected_hash = hashlib.sha256(data).hexdigest()

    saved = [
        storage.save(user_id=user_id, cat_id=cat_id, data=data, suffix=".jpg"),
        storage.save_reference(
            user_id=user_id, cat_id=cat_id, kind="side", data=data, suffix=".png"
        ),
        storage.save_profile_photo(user_id=user_id, data=data, suffix=".jpg"),
        storage.save_chat_photo(
            conversation_id=conversation_id, message_id=message_id, data=data, suffix=".jpg"
        ),
        storage.save_post_photo(post_id=post_id, data=data, suffix=".jpg"),
    ]

    for key, content_hash in saved:
        assert content_hash == expected_hash
        assert expected_hash in key
        assert storage.open(key).read_bytes() == data


def test_avatar_asset_keeps_versioned_key(tmp_path):
    storage = PrivatePhotoStorage(tmp_path)
    user_id, cat_id = uuid4(), uuid4()

    key, content_hash = storage.save_avatar_asset(
        user_id=user_id, cat_id=cat_id, version=2,
        name="preview", data=b"asset", suffix=".png",
    )

    assert key == storage.avatar_key_for(user_id, cat_id, 2, "preview", ".png")
    assert content_hash == hashlib.sha256(b"asset").hexdigest()
    assert storage.open(key).read_bytes() == b"asset"
