from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.persistence_models import User, ProductState
from app.routers.auth import change_password, request_change_email, confirm_change_email
from app.routers.product import export_user_data, submit_feedback, get_favorites, update_favorites
from app.schemas import (
    ChangePasswordRequest,
    ChangeEmailRequest,
    ConfirmChangeEmailRequest,
    FeedbackRequest,
    FavoritesSyncRequest,
)
from app.services.auth_service import password_hash


@pytest.mark.asyncio
async def test_change_password_success():
    hashed = password_hash.hash("AntigaSenhaForte123!")
    user = User(
        id=uuid.uuid4(),
        email="gateiro@gatalogo.app",
        username="gateiro",
        status="active",
        password_hash=hashed,
    )
    session = AsyncMock()
    payload = ChangePasswordRequest(
        current_password="AntigaSenhaForte123!",
        new_password="NovaSenhaMuitoForte456!",
    )

    await change_password(payload, user=user, session=session)
    assert password_hash.verify("NovaSenhaMuitoForte456!", user.password_hash)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_password_wrong_current():
    hashed = password_hash.hash("AntigaSenhaForte123!")
    user = User(
        id=uuid.uuid4(),
        email="gateiro@gatalogo.app",
        username="gateiro",
        status="active",
        password_hash=hashed,
    )
    session = AsyncMock()
    payload = ChangePasswordRequest(
        current_password="SenhaErrada999!",
        new_password="NovaSenhaMuitoForte456!",
    )

    with pytest.raises(HTTPException) as exc_info:
        await change_password(payload, user=user, session=session)
    assert exc_info.value.status_code == 400
    assert "Senha atual incorreta" in exc_info.value.detail


@pytest.mark.asyncio
async def test_change_email_flow():
    user = User(
        id=uuid.uuid4(),
        email="velho@gatalogo.app",
        username="gateiro",
        status="active",
    )
    session = AsyncMock()
    # Mock no existing user with new email
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=exec_result)

    with patch("app.routers.auth.create_otp", new_callable=AsyncMock) as mock_create_otp:
        req_res = await request_change_email(
            ChangeEmailRequest(new_email="novo@gatalogo.app"),
            user=user,
            session=session,
        )
        assert "Código de confirmação" in req_res["message"]
        mock_create_otp.assert_awaited_once()

    with patch("app.routers.auth.consume_otp", new_callable=AsyncMock) as mock_consume_otp:
        conf_res = await confirm_change_email(
            ConfirmChangeEmailRequest(new_email="novo@gatalogo.app", code="123456"),
            user=user,
            session=session,
        )
        assert conf_res["email"] == "novo@gatalogo.app"
        assert user.email == "novo@gatalogo.app"
        mock_consume_otp.assert_awaited_once()


@pytest.mark.asyncio
async def test_export_user_data():
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="export@gatalogo.app",
        username="explorador",
        status="active",
    )
    session = AsyncMock()

    mock_state = ProductState(user_id=user_id, preferences={"bio": "Adoro felinos"}, achievements={"first_cat": "2026-09-16T10:00:00Z"})
    with patch("app.routers.product._state", new_callable=AsyncMock) as mock_get_state:
        mock_get_state.return_value = mock_state
        # Mock cats, reports, sightings execution queries
        cat_exec = MagicMock()
        cat_exec.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=cat_exec)

        data = await export_user_data(user=user, session=session)
        assert "exported_at" in data
        assert data["account"]["username"] == "explorador"
        assert data["account"]["preferences"]["bio"] == "Adoro felinos"
        assert isinstance(data["cats"], list)
        assert isinstance(data["missing_reports"], list)
        assert isinstance(data["sightings"], list)


@pytest.mark.asyncio
async def test_submit_feedback():
    user = User(id=uuid.uuid4(), email="user@gatalogo.app", username="gateiro")
    res = await submit_feedback(
        FeedbackRequest(
            category="bug",
            subject="Erro na foto",
            message="Ocorreu um problema ao carregar imagem no modelo antigo.",
        ),
        user=user,
    )
    assert res["status"] == "received"
    assert res["category"] == "bug"
    assert "id" in res


@pytest.mark.asyncio
async def test_favorites_sync():
    user_id = uuid.uuid4()
    user = User(id=user_id, email="fav@gatalogo.app", username="gateiro")
    session = AsyncMock()
    state = ProductState(user_id=user_id, preferences={"favorites": ["cat-1", "cat-2"]})

    with patch("app.routers.product._state", new_callable=AsyncMock) as mock_get_state:
        mock_get_state.return_value = state
        favs = await get_favorites(user=user, session=session)
        assert "cat-1" in favs["favorites"]
        assert "cat-2" in favs["favorites"]

        updated = await update_favorites(
            FavoritesSyncRequest(favorites=["cat-1", "cat-3"]),
            user=user,
            session=session,
        )
        assert set(updated["favorites"]) == {"cat-1", "cat-3"}
        assert state.preferences["favorites"] == updated["favorites"]
        session.commit.assert_awaited_once()
