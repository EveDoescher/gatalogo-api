"""All RES-05 variants, using own Postgres/files/SMTP and fake paid providers."""

import pytest
from unittest.mock import AsyncMock
from sqlalchemy import select

pytest.register_assert_rewrite(
    "storage_resilience_cases", "image_cases", "pipeline_cases", "delivery_cases",
    "fcm_delivery_cases", "current_upload_transport_cases",
)
pytest_plugins = ["security_cases", "extended_cases", "current_upload_transport_cases", "delivery_cases"]

from storage_resilience_cases import (  # noqa: F401
    own_primary,
    upload,
    test_RES05_IMG07_storage_write_failure_rollback_and_retry,
    test_RES05_RSC03_delete_failure_after_commit_recovery,
    test_RES05_disposable_database_short_outage_and_recovery,
)
from image_cases import (  # noqa: F401
    test_RES05_provider_response_schema,
    test_RES05_provider_retry_boundaries,
)
from pipeline_cases import (  # noqa: F401
    test_LAT08_DB05_paused_inference_allows_health_sync_and_chat as test_RES05_slow_provider,
)
from delivery_cases import (  # noqa: F401
    test_DLV02_SMTP_ambiguous_acceptance_retry_and_cooldown as test_RES05_SMTP_ambiguous_acceptance,
    test_DLV02_local_socket_outage_cooldown_and_recovery as test_RES05_SMTP_outage_recovery,
)
from fcm_delivery_cases import (  # noqa: F401
    test_DLV04_receipt_survives_connection_reset_and_retries_only_pending_device as test_RES05_FCM_unavailable_recovery,
)
from security_cases import evidence, uid
from app.persistence_models import CatPhoto, RemoteCat
from app.services.photo_cleanup import drain_photo_cleanup, queue_photo_cleanup
from app.routers import sync
from image_cases import configured


@pytest.mark.asyncio
async def test_RES05_upload_commit_failure_preserves_old_and_recovers_new_orphan(db, monkeypatch):
    old, key, data = await own_primary(db)
    monkeypatch.setattr(sync, "refresh_cat_matches", AsyncMock())
    real = sync.AsyncSession.commit

    async def failed_commit(session):
        raise RuntimeError("controlled upload commit failure")

    monkeypatch.setattr(sync.AsyncSession, "commit", failed_commit)
    with pytest.raises(RuntimeError, match="controlled upload commit"):
        await upload(db, data)
    monkeypatch.setattr(sync.AsyncSession, "commit", real)
    candidates = list(db.storage.absolute_path(".photo-cleanup").glob("*.json"))
    assert len(candidates) == 2
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("res-storage-cat"))
        assert cat.photo_key == key
        result = await drain_photo_cleanup(session)
    assert result == {"deleted": 1, "retained": 1, "failed": 0}
    assert db.storage.open(key).read_bytes() == old
    assert len(list(db.storage.root.rglob("*.png"))) == 1
    assert (await upload(db, data)).status_code == 204
    evidence("RES05-upload-commit-failure", {
        "scenario": "RES-05", "committed_file_preserved": True,
        "uncommitted_file_cleaned": True, "retry": 204, "cleanup": result,
    })


@pytest.mark.asyncio
async def test_RES05_cleanup_defers_while_writer_holds_cat_lock(db):
    old, key, _ = await own_primary(db)
    async with db.factory() as writer:
        cat = await writer.scalar(select(RemoteCat).where(
            RemoteCat.id == uid("res-storage-cat"),
        ).with_for_update())
        queue_photo_cleanup(cat, [key])
        cat.photo_key = None
        await writer.flush()
        async with db.factory() as cleaner:
            deferred = await drain_photo_cleanup(cleaner)
        assert deferred == {"deleted": 0, "retained": 0, "failed": 1}
        assert db.storage.open(key).read_bytes() == old
        await writer.rollback()
    async with db.factory() as cleaner:
        recovered = await drain_photo_cleanup(cleaner)
    assert recovered == {"deleted": 0, "retained": 1, "failed": 0}
    assert db.storage.open(key).read_bytes() == old
    evidence("RES05-cleanup-writer-lock", {
        "scenario": "RES-05", "lock_deferral": deferred,
        "after_writer_rollback": recovered, "referenced_file_preserved": True,
    })


@pytest.mark.parametrize("failure", ["timeout", "network"])
def test_RES05_provider_transient_failure_recovers(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from app.services import analysis_service

    service = configured(tmp_path, monkeypatch)
    service.settings.gemini_max_retries = 1
    calls, delays = [], []
    expected = object()

    def generate(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            if failure == "timeout":
                raise TimeoutError("controlled timeout")
            raise ConnectionError("controlled connecterror")
        return expected

    monkeypatch.setattr(analysis_service, "sleep", delays.append)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    assert service._generate_content_with_retry(client, contents=[], config=None) is expected
    assert calls == [1, 1] and delays == [1]
    evidence("RES05-provider-recovery-" + failure, {
        "scenario": "RES-05", "fault": failure,
        "provider_calls": 2, "backoff_seconds": delays, "recovered": True,
        "scope": "Injected provider transport exception; no external inference",
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["primary", "reference", "legacy-separators"])
async def test_RES05_cleanup_never_deletes_referenced_file(db, reference):
    old, key, _ = await own_primary(db)
    async with db.factory() as session:
        cat = await session.scalar(select(RemoteCat).where(
            RemoteCat.id == uid("res-storage-cat"),
        ).with_for_update())
        queue_photo_cleanup(cat, [key])
        if reference == "reference":
            cat.photo_key = None
        elif reference == "legacy-separators":
            cat.photo_key = key.replace("/", "\\")
            photo = await session.scalar(select(CatPhoto))
            photo.storage_key = cat.photo_key
        # Also exercise a queued candidate surviving a rolled back transaction.
        if reference == "primary":
            cat.photo_key = None
            await session.rollback()
        else:
            await session.commit()
    async with db.factory() as session:
        result = await drain_photo_cleanup(session)
    assert result == {"deleted": 0, "retained": 1, "failed": 0}
    assert db.storage.open(key).read_bytes() == old
    evidence("RES05-retained-" + reference, {
        "scenario": "RES-05", "reference": reference,
        "referenced_file_preserved": True, "cleanup": result,
    })
