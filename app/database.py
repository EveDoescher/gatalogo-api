"""Banco assíncrono da conta e do catálogo remoto.

O esquema é criado exclusivamente por migrações Alembic. A aplicação não faz
``create_all`` em produção, evitando alterações silenciosas no banco.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_database_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
