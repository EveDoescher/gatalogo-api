from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.schemas import MessageCreate, MessageResponse
from app.services.storage_service import photo_storage
from app.services.websocket_manager import ConnectionManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_connection_manager_presence_and_last_seen():
    manager = ConnectionManager()
    user_id = uuid4()
    socket = FakeWebSocket()

    assert not manager.is_online(user_id)
    assert manager.get_last_seen(user_id) is None

    await manager.connect(user_id, socket)
    assert manager.is_online(user_id)
    assert manager.get_last_seen(user_id) is None

    await manager.disconnect(user_id, socket)
    assert not manager.is_online(user_id)
    assert manager.get_last_seen(user_id) is not None


@pytest.mark.asyncio
async def test_chat_photo_storage(tmp_path):
    storage = photo_storage
    conv_id = uuid4()
    msg_id = uuid4()
    sample_bytes = b"test_jpeg_bytes_for_chat"

    key, content_hash = storage.save_chat_photo(
        conversation_id=conv_id,
        message_id=msg_id,
        data=sample_bytes,
        suffix=".jpg",
    )

    assert key.startswith("conversations/")
    assert content_hash
    path = storage.open(key)
    assert path.exists()
    assert path.read_bytes() == sample_bytes

    # Clean up
    storage.delete(key)
    assert not path.exists()


def test_message_schemas():
    # Test photo message
    msg_photo = MessageCreate(
        body="Olha o gato!",
        type="photo",
        media_url="/social/conversations/123/photo/abc.jpg",
    )
    assert msg_photo.type == "photo"
    assert msg_photo.media_url

    # Test location message
    msg_loc = MessageCreate(
        body="",
        type="location",
        location_lat=-23.5505,
        location_lng=-46.6333,
        location_title="Praça da Sé",
    )
    assert msg_loc.type == "location"
    assert msg_loc.location_lat == -23.5505

    # Test cat card message
    msg_cat = MessageCreate(
        body="",
        type="cat_card",
        cat_data={
            "cat_id": str(uuid4()),
            "name": "Mingau",
            "capture_number": 1,
            "coat_type": "Rajado",
            "primary_color": "Cinza",
        },
    )
    assert msg_cat.type == "cat_card"
    assert msg_cat.cat_data["name"] == "Mingau"

    # Test MessageResponse model
    msg_id = uuid4()
    conv_id = uuid4()
    sender_id = uuid4()
    now = datetime.now(UTC)
    resp = MessageResponse(
        id=msg_id,
        conversation_id=conv_id,
        sender_id=sender_id,
        body="Mensagem de teste",
        type="text",
        created_at=now,
    )
    assert resp.is_read is False
    assert resp.is_edited is False
    assert resp.is_deleted is False


def test_user_avatar_url_helper(tmp_path):
    from app.routers.social import _user_avatar_url
    user_without_avatar = uuid4()
    # No preferences
    assert _user_avatar_url(user_without_avatar, None) is None
    # Preferences with no avatar_url
    assert _user_avatar_url(user_without_avatar, {}) is None
    # Preferences with avatar_url, but file does not exist
    assert _user_avatar_url(user_without_avatar, {"avatar_url": f"/app/account/avatar/{user_without_avatar}"}) is None

    # Now create the actual file
    user_with_avatar = uuid4()
    photo_storage.save_profile_photo(user_id=user_with_avatar, data=b"fake_avatar_data", suffix=".jpg")
    url = _user_avatar_url(user_with_avatar, {"avatar_url": f"/app/account/avatar/{user_with_avatar}"})
    assert url == f"/app/account/avatar/{user_with_avatar}"

