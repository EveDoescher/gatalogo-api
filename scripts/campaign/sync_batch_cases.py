"""Real current Flutter synchronization; bounded fixtures, no model/provider calls."""

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, func

pytest.register_assert_rewrite("current_offline_cases")
pytest_plugins = ["current_offline_cases"]
from current_offline_cases import client_hashes
from critical_report import flutter_tests
from security_cases import evidence
from subprocesses import run_logged
from app.persistence_models import HabitatWallet, RemoteCat, SyncChange, User
from app.services.auth_service import issue_tokens


@pytest.mark.asyncio
async def test_LAT04_current_client_large_dirty_batches_and_growing_history(own_api):
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "sync-batches"
    folder.mkdir(parents=True, exist_ok=False)
    repo = Path(os.environ["QUALITY_FLUTTER_REPO"]).resolve()
    before = client_hashes(repo)
    stamp = datetime(2026, 10, 25, tzinfo=UTC)
    large_trials = int(os.environ.get("QUALITY_LARGE_BATCH_TRIALS", "1"))
    assert 1 <= large_trials <= 5
    variants, accounts = [], []
    async with own_api.factory() as session:
        for count, history, failure in [
            (0, 1, False),
            (1, 1, False),
            (40, 1, False),
            (501, 1, False),
            (5, 10, False),
            (5, 100, False),
            (501, 1, True),
        ]:
            owner = uuid4()
            user = User(
                id=owner, email=f"batch-{owner.hex}@example.invalid", status="active"
            )
            session.add(user)
            await session.flush()
            session.add(HabitatWallet(user_id=owner, balance=100))
            bundle = await issue_tokens(session, user=user, device_id="batch-fixture")
            accounts.append({"owner": str(owner), "token": bundle["access_token"]})
            ids = [uuid4() for _ in range(count)]
            variants.append(
                {
                    "name": f"n{count}-h{history}-failure{int(failure)}",
                    "ids": [str(i) for i in ids],
                    "history": history,
                    "failure": failure,
                    "trials": (1 if failure else large_trials) if count >= 500 else 3,
                }
            )
            if failure:
                # Start empty: first 500 commit before the injected second failure.
                continue
            # Equal versions exercise the public replay contract without real inference.
            for i, ident in enumerate(ids):
                cat = RemoteCat(
                    id=uuid4(),
                    user_id=owner,
                    client_id=ident,
                    payload={
                        "name": f"batch-{i}",
                        "status": "pending_analysis",
                        "identity_state": "pending",
                    },
                    client_updated_at=stamp,
                    updated_by_device="batch-fixture",
                )
                session.add(cat)
                await session.flush()
                session.add_all(
                    [SyncChange(user_id=owner, cat_id=cat.id) for _ in range(history)]
                )
        await session.commit()
    fixtures = folder / "fixtures.json"
    fixtures.write_text(
        json.dumps(
            {"root": str(folder), "stamp": stamp.isoformat(), "variants": variants}
        ),
        encoding="utf-8",
    )
    manifest = {
        "flutter_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip(),
        "evaluated_content_sha256": before,
    }
    (folder / "client-source-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    sdk = Path(os.environ.get("FLUTTER_SDK", "C:/flutter"))
    with (folder / "flutter.jsonl").open("w", encoding="utf-8") as log:
        result = await asyncio.to_thread(
            run_logged,
            [
                str(sdk / "bin/cache/dart-sdk/bin/dart.exe"),
                str(sdk / "bin/cache/flutter_tools.snapshot"),
                "test",
                "--no-pub",
                "--machine",
                "--timeout=180s",
                f"--dart-define=API_BASE_URL={own_api.url}",
                "tool/campaign_sync_batches_test.dart",
            ],
            cwd=repo,
            env=dict(
                os.environ,
                QUALITY_SYNC_FIXTURES=str(fixtures),
                QUALITY_SYNC_ACCOUNTS=json.dumps(accounts),
            ),
            log=log,
            timeout=1050 if large_trials > 1 else 360,
        )
    cases = flutter_tests(folder / "flutter.jsonl")
    unchanged = client_hashes(repo) == before
    evidence(
        "LAT04-client-batches",
        {
            "flutter_sha": manifest["flutter_sha"],
            "client_sources_unchanged": unchanged,
            "cases": cases,
            "paid_calls": len(own_api.provider_calls),
            "large_normal_trials": large_trials,
            "scope": "Real SQLite/client/own API; fresh local databases, shared seeded server replay state. Five large normal trials in confirmation mode bound observed ~100 s/case cost while exposing variability. Small datasets use three trials; large fault cohort uses one functional recovery trial. No independent-cold-server, normality, P99 or performance-gain claim.",
        },
    )
    assert unchanged
    assert len(own_api.provider_calls) == 0
    assert (
        result.returncode == 0
        and len(cases) == sum(v["trials"] for v in variants)
        and all(c["result"] == "success" and not c["skipped"] for c in cases)
    )
    async with own_api.factory() as session:
        for account, variant in zip(accounts, variants, strict=True):
            assert await session.scalar(
                select(func.count())
                .select_from(RemoteCat)
                .where(RemoteCat.user_id == UUID(account["owner"]))
            ) == len(variant["ids"])
            cursor = (
                await session.scalar(
                    select(func.max(SyncChange.revision)).where(
                        SyncChange.user_id == UUID(account["owner"])
                    )
                )
                or 0
            )
            for trial in range(variant["trials"]):
                metrics = json.loads(
                    (folder / f"{variant['name']}-{trial}" / "metrics.json").read_text()
                )
                assert int(metrics["cycles"][-1]["cursor"]) == cursor
