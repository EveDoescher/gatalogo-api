"""Avatar choices persist across account reloads and remain visible socially."""
from io import BytesIO

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic import ValidationError

from test_progression import account as account_db
from app.routers import product, social


@pytest.mark.asyncio
async def test_presets_persist_without_overwriting_preferences(account_db):
    db, user = account_db
    await product.update_account(product.UpdateAccountRequest(bio="Meu cantinho"), user, db)
    for preset in ("bapinho", "bapinha"):
        result = await product.update_account(
            product.UpdateAccountRequest(avatar_preset=preset), user, db)
        url = f"/app/account/avatar/preset/{preset}"
        assert result["avatar_url"] == url
        db.expire_all()
        await db.refresh(user)
        saved = await product.account(user, db)
        assert saved["avatar_url"] == url
        assert saved["bio"] == "Meu cantinho"
        assert social._user_avatar_url(user.id, saved["preferences"]) == url
        edited = await product.update_account(product.UpdateAccountRequest(bio="Nova bio"), user, db)
        assert edited["avatar_url"] == url
        await product.update_account(product.UpdateAccountRequest(bio="Meu cantinho"), user, db)


@pytest.mark.asyncio
async def test_public_presets_are_images_and_unknown_names_are_rejected():
    app = FastAPI()
    app.include_router(product.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for preset in ("bapinho", "bapinha"):
            response = await client.get(f"/app/account/avatar/preset/{preset}")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            Image.open(BytesIO(response.content)).verify()
        assert (await client.get("/app/account/avatar/preset/unknown")).status_code == 422
    with pytest.raises(ValidationError):
        product.UpdateAccountRequest(avatar_preset="unknown")


@pytest.mark.asyncio
async def test_photo_replaces_preset_and_has_fresh_cache_key(account_db, monkeypatch):
    db, user = account_db
    uploads = []
    monkeypatch.setattr(product.photo_storage, "save_profile_photo", lambda **args: uploads.append(args))
    await product.update_account(product.UpdateAccountRequest(avatar_preset="bapinha"), user, db)
    data = BytesIO()
    Image.new("RGB", (20, 20), "green").save(data, format="PNG")
    urls = []
    for _ in range(2):
        result = await product.upload_avatar(
            UploadFile(filename="portrait.png", file=BytesIO(data.getvalue())), user, db)
        urls.append(result["avatar_url"])
        assert result["avatar_url"].startswith(f"/app/account/avatar/{user.id}?v=")
        assert (await product.account(user, db))["avatar_url"] == result["avatar_url"]
    assert urls[0] != urls[1]
    assert uploads[0]["data"] == data.getvalue()
    switched_back = await product.update_account(
        product.UpdateAccountRequest(avatar_preset="bapinho"), user, db)
    assert switched_back["avatar_url"].endswith("/preset/bapinho")
