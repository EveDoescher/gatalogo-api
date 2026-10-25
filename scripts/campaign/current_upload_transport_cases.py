"""Reuse three actual TCP fault windows against the current isolated API only."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

pytest.register_assert_rewrite("current_offline_cases", "upload_transport_cases")
pytest_plugins = ["current_offline_cases"]
from upload_transport_cases import (  # noqa: F401
    test_RES02_DATA02_real_upload_transport_windows_preserve_atomic_file_and_retry,
)
from app.routers import sync


@pytest_asyncio.fixture
async def live(own_api, monkeypatch):
    async def no_matching(session, *, cat, settings):
        pass

    # A resource probe must not retain every ORM argument in mock call history.
    monkeypatch.setattr(sync, "refresh_cat_matches", no_matching)
    async with (
        httpx.AsyncClient(
            base_url=own_api.url,
            timeout=10,
            headers={"Authorization": "Bearer " + own_api.bundles[0]["access_token"]},
        ) as a,
        httpx.AsyncClient(
            base_url=own_api.url,
            timeout=10,
            headers={"Authorization": "Bearer " + own_api.bundles[1]["access_token"]},
        ) as other,
    ):
        yield SimpleNamespace(
            url=own_api.url, db=own_api.factory, uid=own_api.ids[0], a=a, other=other
        )
    assert own_api.provider_calls == []
