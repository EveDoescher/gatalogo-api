"""Append catalogue revisions within the caller transaction."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import RemoteCat, SyncChange


async def record_change(session: AsyncSession, cat: RemoteCat) -> None:
    session.add(SyncChange(user_id=cat.user_id, cat_id=cat.id))
    await session.flush()
