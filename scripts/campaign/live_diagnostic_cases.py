"""Re-run the unmodified failing live scenario while capturing domain evidence."""

import json
import os
from pathlib import Path
import sys
import pytest
from sqlalchemy import select

SOURCE = Path(os.environ["QUALITY_TARGET_SOURCE"]).resolve()
assert SOURCE.is_relative_to(Path(os.environ["QUALITY_CAMPAIGN_RUN"]) / "sources")
sys.path.insert(0, str(SOURCE / "tests"))
pytest.register_assert_rewrite("test_quintal_live_api")
import test_quintal_live_api as original
from app.persistence_models import ProductState, ProgressEvent, HabitatTransaction

pytest_plugins = ["test_quintal_live_api"]


@pytest.mark.asyncio
async def test_live_routine_reward_diagnostic(live, monkeypatch):
    captured = []
    previous = live.a.get

    async def observe(path, *args, **kwargs):
        response = await previous(path, *args, **kwargs)
        if path == "/app/progress":
            captured.append(response.json())
        return response

    monkeypatch.setattr(live.a, "get", observe)
    try:
        await original.test_live_build_plant_water_cook_queue_cancel_and_collect(live)
    finally:
        async with live.db() as db:
            state = await db.get(ProductState, live.uid)
            events = (
                await db.scalars(
                    select(ProgressEvent).where(ProgressEvent.user_id == live.uid)
                )
            ).all()
            activity = (
                await db.scalars(
                    select(HabitatTransaction).where(
                        HabitatTransaction.user_id == live.uid,
                        HabitatTransaction.category == "quintal_activity",
                    )
                )
            ).all()
            evidence = {
                "scenario": "DATA-07",
                "original_test": "test_live_build_plant_water_cook_queue_cancel_and_collect",
                "progress_responses": captured,
                "routine_preferences": {
                    k: state.preferences.get(k)
                    for k in ("quintal_routine_spaces", "milestone_daily_offers")
                },
                "events": [
                    {
                        "kind": e.kind,
                        "state": e.state,
                        "event_key": e.event_key,
                        "created_at": e.created_at,
                        "evidence": e.evidence,
                    }
                    for e in events
                ],
                "activity_journal": [t.details for t in activity],
                "scope": "Original HTTP scenario unchanged. Own disposable account only; no auth headers, tokens or private user data captured.",
            }
        folder = Path(os.environ["QUALITY_CASE_EVIDENCE"])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "routine-reward-diagnostic.json"
        assert not path.exists()
        path.write_text(
            json.dumps(evidence, default=str, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


@pytest.mark.asyncio
async def test_live_disabled_rewards_activity(live):
    from datetime import datetime, UTC, timedelta
    from uuid import UUID
    from app.quintal_models import QuintalTask

    before = (await live.a.get("/app/progress")).json()
    assert before["rewards_enabled"] is False
    started = await original.send(live.a, original.command("cook", "apple"))
    ident = started["outcome"]["task_id"]
    async with live.db() as db:
        task = await db.get(QuintalTask, UUID(ident))
        task.started_at = datetime.now(UTC) - timedelta(hours=1)
        task.ready_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    collected = await original.send(live.a, original.command("collect", ident))
    assert collected["production"]["stock"]["ration:apple"] == 3
    view = (await live.a.get("/app/progress")).json()
    routine = next(
        row for row in view["daily_missions"] if row.get("group") == "quintal_routine"
    )
    assert (
        routine["progress"] == 1
        and not routine["completed"]
        and not routine["reward_received"]
    )
    async with live.db() as db:
        events = (
            await db.scalars(
                select(ProgressEvent).where(ProgressEvent.user_id == live.uid)
            )
        ).all()
        cooking = [e for e in events if e.evidence.get("metric") == "quintal_cook"]
        assert cooking and all(
            e.state == "recorded" and not e.evidence["eligible"] for e in cooking
        )
        assert not any(
            e.event_key.startswith("mission:daily_quintal_routine:") for e in events
        )
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "routine-disabled.json"
    assert not path.exists()
    path.write_text(
        json.dumps(
            {
                "scenario": "DATA-07",
                "rewards_enabled": False,
                "production_collected": 3,
                "routine_progress": 1,
                "mission_reward_received": False,
                "activity_recorded_without_reward": True,
                "scope": "Explicit 0% rollout only in discarded API; original user services unchanged",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
