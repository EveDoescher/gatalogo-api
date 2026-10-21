from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from app.persistence_models import (
    HabitatWallet,
    User,
)
from app.schemas import (
    PublicUserResponse,
)
from app.services.audience import permits
from app.services.storage_service import photo_storage


def _user_avatar_url(user_id: UUID, preferences: dict | None) -> str | None:
    avatar_url = (preferences or {}).get("avatar_url") if preferences else None
    if not avatar_url:
        return None
    if avatar_url in (
        "/app/account/avatar/preset/bapinho",
        "/app/account/avatar/preset/bapinha",
    ):
        return avatar_url
    try:
        path = photo_storage.absolute_path(
            photo_storage.profile_key_for(user_id, ".jpg")
        )
        if path.parent.is_dir() and any(path.parent.glob("avatar*")):
            return avatar_url
    except Exception:
        pass
    return None


def _public_user_response(
    user: User, preferences: dict | None = None
) -> PublicUserResponse:
    if not user.username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Escolha um nome de usuário para usar recursos sociais.",
        )
    avatar_url = (
        _user_avatar_url(user.id, preferences) if preferences is not None else None
    )
    bio = (preferences or {}).get("bio") if preferences is not None else None
    return PublicUserResponse(
        id=user.id, username=user.username, avatar_url=avatar_url, bio=bio
    )


async def _habitat_preview(session, viewer_id, owner_id, as_friend=False):
    if not await permits(
        session,
        viewer_id,
        owner_id,
        "habitat",
        as_friend=as_friend and viewer_id == owner_id,
    ):
        return None
    wallet = await session.get(HabitatWallet, owner_id)
    if wallet is None:
        return {"cats_count": 0, "expansion_level": 0}
    return {
        "cats_count": len(wallet.selected_cat_ids or []),
        "expansion_level": wallet.expansion_level,
    }
