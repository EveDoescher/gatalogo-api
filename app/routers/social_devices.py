from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.dependencies import current_user
from app.persistence_models import (
    DeviceToken,
    User,
)
from app.schemas import (
    DeviceTokenRequest,
)

router = APIRouter(prefix="/social", tags=["social"])


@router.put("/device-token", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(
    payload: DeviceTokenRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_database_session),
) -> None:
    existing = (
        await session.execute(
            select(DeviceToken)
            .where(DeviceToken.token == payload.token)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            DeviceToken(
                user_id=user.id,
                token=payload.token,
                platform=payload.platform,
                preferences=payload.preferences,
            )
        )
    else:
        existing.user_id, existing.platform, existing.preferences = (
            user.id,
            payload.platform,
            payload.preferences,
        )
    await session.commit()
