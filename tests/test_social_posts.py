from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.persistence_models import FeedItem, User, ProgressEvent
from app.routers import social_feed as social


@pytest_asyncio.fixture
async def post_account(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for model in (User, FeedItem, ProgressEvent):
            await connection.run_sync(model.__table__.create)
    monkeypatch.setattr(social.progress, 'account', AsyncMock())
    monkeypatch.setattr(social.progress, 'grant', AsyncMock(return_value=SimpleNamespace(result=True)))
    monkeypatch.setattr(social, '_friend_ids', AsyncMock(return_value=[]))
    monkeypatch.setattr(social, 'publish_committed_events', AsyncMock())
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(id=uuid4(), email='posts@test.local', username='writer', status='active')
        session.add(user)
        await session.commit()
        yield session, user
    await engine.dispose()


@pytest.mark.asyncio
async def test_text_post_and_retry_use_the_same_record(post_account):
    session, user = post_account
    request = social.CatPostCreate(note='Passeio na praça hoje.', request_id=uuid4(), is_public=False)
    first = await social.create_post(request, user, session)
    second = await social.create_post(request, user, session)
    assert first.id == second.id
    assert first.payload['cat_name'] == '' and first.payload['photo_url'] is None
    assert first.payload['is_public'] is False
    assert len(list((await session.scalars(select(FeedItem))).all())) == 1
    changed = request.model_copy(update={'note': 'Outro conteúdo'})
    with pytest.raises(HTTPException) as error:
        await social.create_post(changed, user, session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_empty_post_and_foreign_photo_are_rejected(post_account):
    session, user = post_account
    with pytest.raises(HTTPException) as error:
        await social.create_post(social.CatPostCreate(note='  '), user, session)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        await social.create_post(social.CatPostCreate(note='Minha publicação', photo_url='/social/posts/photo/foreign.jpg'), user, session)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        await social.create_post(social.CatPostCreate(photo_base64='not valid'), user, session)
    assert error.value.status_code == 422
