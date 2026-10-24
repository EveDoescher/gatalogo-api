"""Late worker results must have no observable effects on newer catalogue state."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW, reference_claim
from app.config import get_settings
from app.persistence_models import (
    CatPhoto,
    RemoteCat,
    VisionJob,
    SyncChange,
    HabitatWallet,
)
from app.services import realtime_events


async def snapshot(db, claim):
    async with db.factory() as session:
        photo = await session.get(CatPhoto, claim.photo_id)
        cat = await session.get(RemoteCat, claim.cat_id)
        return dict(
            photo_analysis=photo.analysis,
            photo_status=photo.status,
            photo_hash=photo.content_hash,
            photo_deleted=photo.deleted_at,
            cat_analysis=cat.analysis,
            cat_payload=cat.payload,
            cat_deleted=cat.deleted_at,
            balance=(await session.get(HabitatWallet, uid("A"))).balance,
            revisions=await session.scalar(
                select(func.count()).select_from(SyncChange)
            ),
        )


def event_spy(monkeypatch, worker):
    send = AsyncMock()
    monkeypatch.setattr(realtime_events.ws_manager, "send_to_user", send)
    monkeypatch.setattr(
        worker, "publish_committed_events", realtime_events.publish_committed_events
    )
    return send


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cat-delete", "photo-delete", "replace-hash"])
async def test_DATA06_obsolete_finish_has_zero_catalogue_reward_or_event_effect(
    db, monkeypatch, change
):
    worker, claim = await reference_claim(db, monkeypatch)
    send = event_spy(monkeypatch, worker)
    async with db.factory() as session:
        cat = await session.get(RemoteCat, claim.cat_id)
        photo = await session.get(CatPhoto, claim.photo_id)
        if change == "cat-delete":
            cat.deleted_at = NOW
        elif change == "photo-delete":
            photo.deleted_at = NOW
        else:
            photo.content_hash = "b" * 64
        await session.commit()
    before = await snapshot(db, claim)
    await worker._finish(db.factory, claim, get_settings(), incoming={"obsolete": True})
    assert await snapshot(db, claim) == before
    worker.confirm_photo.assert_not_awaited()
    send.assert_not_awaited()
    async with db.factory() as session:
        assert (await session.get(VisionJob, claim.job_id)).status == "superseded"
    evidence(
        "DATA06-full-state-" + change,
        dict(
            change=change,
            catalogue_analysis_status_hash_revision_and_balance_unchanged=True,
            success_events=0,
            job_status="superseded",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("late_after_current", [False, True])
async def test_DATA06_reclaimed_attempt_discards_old_result_before_and_after_new_completion(
    db, monkeypatch, late_after_current
):
    worker, old = await reference_claim(db, monkeypatch)
    send = event_spy(monkeypatch, worker)
    settings = get_settings()
    async with db.factory() as session:
        job = await session.get(VisionJob, old.job_id)
        job.started_at = datetime.now(UTC) - timedelta(
            seconds=settings.vision_job_lease_seconds + 1
        )
        await session.commit()
    current = await worker._claim(db.factory, settings)
    assert current.attempt == old.attempt + 1
    if late_after_current:
        await worker._finish(db.factory, current, settings, incoming={"current": True})
    before = await snapshot(db, old)
    previous_events = send.await_count
    previous_progression = worker.confirm_photo.await_count
    await worker._finish(db.factory, old, settings, incoming={"obsolete": True})
    assert await snapshot(db, old) == before
    assert send.await_count == previous_events
    assert worker.confirm_photo.await_count == previous_progression
    if not late_after_current:
        await worker._finish(db.factory, current, settings, incoming={"current": True})
    state = await snapshot(db, current)
    assert state["photo_analysis"] == {"current": True}
    assert state["photo_status"] == "ready" and state["revisions"] == 1
    worker.confirm_photo.assert_awaited_once()
    evidence(
        "DATA06-full-state-lease-" + str(late_after_current),
        dict(
            old_attempt=old.attempt,
            new_attempt=current.attempt,
            old_arrived_after_current_completion=late_after_current,
            obsolete_effects=0,
            accepted_current_revisions=state["revisions"],
        ),
    )
