"""Disposable worker subprocess for crash/reclaim proof; never imports models."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def run():
    source = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
    assert source.is_relative_to(
        Path(os.environ["QUALITY_CAMPAIGN_RUN"]).resolve() / "sources"
    )
    sys.path.insert(0, str(source))
    from app.services import reference_analysis as worker

    schema = os.environ["QUALITY_WORKER_SCHEMA"]
    assert schema.startswith("reference_") and schema.replace("_", "").isalnum()
    url = os.environ["RECOGNITION_TEST_DATABASE_URL"]
    assert url.endswith("/recognition_test") and ":5432/" not in url
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from types import SimpleNamespace

    settings = SimpleNamespace(
        max_image_bytes=1024, vision_job_lease_seconds=30, vision_job_max_attempts=3
    )
    for name in (
        "account",
        "confirm_photo",
        "rebuild_analysis_from_photos",
        "publish_committed_events",
    ):
        setattr(worker, name, AsyncMock())
    path = Path(os.environ["QUALITY_WORKER_PHOTO_PATH"])
    assert path.is_file()
    worker.photo_storage.open = lambda key: path
    try:
        if os.environ["QUALITY_WORKER_MODE"] == "claim":
            claim = await worker._claim(factory, settings)
            Path(os.environ["QUALITY_WORKER_READY"]).write_text(
                json.dumps({"job_id": str(claim.job_id), "attempt": claim.attempt}),
                encoding="utf-8",
            )
            await asyncio.Event().wait()
        else:

            async def fake(**kwargs):
                return {"source": "restarted-worker-process"}

            assert await worker.process_next(factory, settings=settings, analyzer=fake)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
