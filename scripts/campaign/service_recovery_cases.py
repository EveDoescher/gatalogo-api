"""All RES-05 variants, using own Postgres/files/SMTP and fake paid providers."""

import pytest
from sqlalchemy import select

pytest.register_assert_rewrite(
    "storage_resilience_cases", "image_cases", "pipeline_cases", "delivery_cases",
    "fcm_delivery_cases", "current_upload_transport_cases",
)
pytest_plugins = ["security_cases", "extended_cases", "current_upload_transport_cases", "delivery_cases"]

from storage_resilience_cases import (  # noqa: F401
    own_primary,
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
