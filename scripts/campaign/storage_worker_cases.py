"""Reuse storage/hash oracles on the current frozen candidate and own data."""

import pytest

pytest.register_assert_rewrite(
    "security_cases", "storage_resilience_cases", "worker_race_cases"
)
pytest_plugins = ["security_cases", "test_reference_analysis"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["no-space", "permission"])
async def test_IMG07_failed_write_preserves_committed_photo_and_retry(
    db, monkeypatch, fault
):
    from storage_resilience_cases import (
        test_RES05_IMG07_storage_write_failure_rollback_and_retry as probe,
    )

    await probe(db, monkeypatch, fault)


@pytest.mark.asyncio
async def test_IMG07_missing_file_restored_with_same_owner_bytes(db):
    from storage_resilience_cases import (
        test_IMG07_missing_private_file_then_restored as probe,
    )

    await probe(db)


@pytest.mark.asyncio
async def test_IMG07_claim_hash_change_rejected_before_analysis_then_retried(queue):
    from worker_race_cases import (
        test_IMG07_bytes_changed_between_claim_and_read_then_retry as probe,
    )

    await probe(queue)
