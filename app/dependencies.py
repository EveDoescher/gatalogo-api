"""Shared HTTP dependencies, independent of route registration."""

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.persistence_models import User
from app.services.auth_service import authenticated_user, invalid_credentials


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_database_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise invalid_credentials()
    return await authenticated_user(authorization.removeprefix("Bearer "), session)
