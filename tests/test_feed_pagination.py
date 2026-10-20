from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.persistence_models import FeedItem, User, Friendship, ProductState
from app.routers import social

@pytest_asyncio.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for model in (User, FeedItem, Friendship, ProductState):
            await connection.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_feed_pagination_skips_private_rows_without_losing_older_posts(db):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID
    viewer = User(email=f"{uuid4()}@test.local", username="reader")
    author = User(email=f"{uuid4()}@test.local", username="writer")
    db.add_all([viewer, author]); await db.flush()
    now = datetime.now(UTC)
    visible = []
    for index in range(27):
        public = index >= 22
        post = FeedItem(id=UUID(int=index+1), user_id=author.id, type="cat_post",
            payload={"is_public": public}, created_at=now if not public else now-timedelta(minutes=1))
        db.add(post)
        if public: visible.append(post)
    await db.flush()
    first = await social.feed(limit=3, cursor=None, cursor_id=None, mode="all", user=viewer, session=db)
    second = await social.feed(limit=3, cursor=first[-1].created_at, cursor_id=first[-1].id,
        mode="all", user=viewer, session=db)
    assert len(first) == 3 and len(second) == 2
    assert [item.id for item in first + second] == sorted([item.id for item in visible], reverse=True)
