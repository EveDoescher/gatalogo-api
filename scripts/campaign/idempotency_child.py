"""Fresh API interpreter for a replay against the parent's disposable schema."""

import asyncio
import json
import os
from pathlib import Path
import re
import sys


async def replay():
    source = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
    run = Path(os.environ["QUALITY_CAMPAIGN_RUN"]).resolve()
    assert source.is_relative_to(run / "sources")
    sys.path.insert(0, str(source))
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.main import app
    from app.database import get_database_session

    url = os.environ["RECOGNITION_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert parsed.database == "recognition_test"
    assert parsed.host == "127.0.0.1" and parsed.port != 5432
    schema = os.environ["QUALITY_REPLAY_SCHEMA"]
    assert re.fullmatch(r"critical_[0-9a-f]{32}", schema)
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema + ",public"}}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_database_session] = database
    request = json.loads(Path(os.environ["QUALITY_REPLAY_REQUEST"]).read_text())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://disposable"
        ) as client:
            reply = await client.post(
                "/app/quintal/operations",
                headers=request["headers"],
                json=request["payload"],
            )
            Path(os.environ["QUALITY_REPLAY_OUTPUT"]).write_text(
                json.dumps(dict(status=reply.status_code, body=reply.json())),
                encoding="utf-8",
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(replay(), timeout=30))
