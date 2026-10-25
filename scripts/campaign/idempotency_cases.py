"""Persisted intents: post-commit response loss, fresh interpreter, queues and scope."""

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW, SOURCE
from app.main import app
from app.database import get_database_session
from app.persistence_models import HabitatWallet, HabitatTransaction
from app.quintal_models import QuintalFacility, QuintalResource, QuintalTask
from app.services import progression
from app.services.quintal_production import operation


async def execute(db, who, action, target, data, key):
    async with db.factory() as session:
        await progression.account(session, uid(who))
        result = await operation(session, uid(who), action, target, data, key, now=NOW)
        await session.commit()
        return result


@pytest.mark.asyncio
async def test_DATA03_lost_response_after_commit_replays_in_fresh_api_process(
    db, monkeypatch
):
    class ResponseLost(RuntimeError):
        pass

    class LostSession(AsyncSession):
        async def commit(self):
            await super().commit()
            raise ResponseLost(
                "controlled response loss after successful database commit"
            )

    lost_factory = async_sessionmaker(
        db.engine, class_=LostSession, expire_on_commit=False
    )

    async def database():
        async with lost_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    original = app.dependency_overrides[get_database_session]
    monkeypatch.setitem(app.dependency_overrides, get_database_session, database)
    payload = dict(
        action="order",
        target="chicken",
        data={"payment": "coins"},
        idempotency_key=str(uid("restart-intent")),
    )
    with pytest.raises(ResponseLost):
        await db.client.post(
            "/app/quintal/operations", headers=db.headers("A"), json=payload
        )
    monkeypatch.setitem(app.dependency_overrides, get_database_session, original)
    async with db.factory() as session:
        schema = await session.scalar(text("SELECT current_schema()"))
        receipt = await session.scalar(
            select(HabitatTransaction).where(
                HabitatTransaction.event_key == "quintal:" + payload["idempotency_key"]
            )
        )
        assert receipt is not None
        expected = receipt.details["result"]
        assert (await session.get(HabitatWallet, uid("A"))).balance == 88
    folder = Path(os.environ["QUALITY_CASE_EVIDENCE"]) / "DATA03-restart"
    folder.mkdir(parents=True, exist_ok=False)
    request, result = folder / "request.json", folder / "response.json"
    request.write_text(
        json.dumps(dict(headers=db.headers("A"), payload=payload)), encoding="utf-8"
    )
    env = dict(
        os.environ,
        QUALITY_REPLAY_SCHEMA=schema,
        QUALITY_REPLAY_REQUEST=str(request),
        QUALITY_REPLAY_OUTPUT=str(result),
    )
    with (folder / "child.log").open("w", encoding="utf-8") as log:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("idempotency_child.py")),
            cwd=SOURCE,
            env=env,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            assert await asyncio.wait_for(child.wait(), timeout=60) == 0
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
    response = json.loads(result.read_text(encoding="utf-8"))
    assert response["status"] == 200 and response["body"]["outcome"] == expected
    serial = await db.client.post(
        "/app/quintal/operations", headers=db.headers("A"), json=payload
    )
    assert serial.status_code == 200 and serial.json()["outcome"] == expected
    async with db.factory() as session:
        assert (await session.get(HabitatWallet, uid("A"))).balance == 88
        assert await session.scalar(select(func.count()).select_from(QuintalTask)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(HabitatTransaction)
                .where(
                    HabitatTransaction.event_key
                    == "quintal:" + payload["idempotency_key"]
                )
            )
            == 1
        )
    evidence(
        "DATA03-postcommit-process-replay",
        dict(
            response_lost_after_real_commit=True,
            fresh_api_interpreter_replay=200,
            serial_replay=200,
            balance=88,
            tasks=1,
            receipts=1,
            outcome_identical=True,
            transport="ASGI; injected response failure after commit, not TCP packet loss",
        ),
    )


@pytest.mark.asyncio
async def test_DATA03_same_cook_intent_reserves_only_one_queue_slot(db):
    async with db.factory() as session:
        session.add(
            QuintalFacility(user_id=uid("A"), kind="kitchen", level=1, target_level=0)
        )
        session.add_all(
            [
                QuintalResource(user_id=uid("A"), resource=k, quantity=n)
                for k, n in (("wheat", 4), ("carrot", 2))
            ]
        )
        await session.commit()
    key = uid("queue-intent")

    async def call():
        return await execute(db, "A", "cook", "garden", {}, key)

    first, parallel = await asyncio.gather(call(), call())
    assert first == parallel == await call()
    async with db.factory() as session:
        assert await session.scalar(select(func.count()).select_from(QuintalTask)) == 1
        assert (await session.get(QuintalResource, (uid("A"), "wheat"))).quantity == 2
        assert (await session.get(QuintalResource, (uid("A"), "carrot"))).quantity == 1
    second = await execute(db, "A", "cook", "garden", {}, uid("second-queue-intent"))
    assert first != second
    assert (
        await call() == first
    )  # Receipt lookup precedes the now-full queue validation.
    async with db.factory() as session:
        assert await session.scalar(select(func.count()).select_from(QuintalTask)) == 2
        assert (await session.get(QuintalResource, (uid("A"), "wheat"))).quantity == 0
        assert (await session.get(QuintalResource, (uid("A"), "carrot"))).quantity == 0
    evidence(
        "DATA03-queue-replay",
        dict(
            parallel_and_serial_same_result=True,
            slots_per_intent=1,
            distinct_intents=2,
            tasks=2,
            replay_when_queue_full=True,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", ["action", "target", "data"])
async def test_DATA04_fingerprint_conflict_and_cross_account_scope(db, difference):
    key = uid("fingerprint-key")
    original = await execute(db, "A", "order", "chicken", {"payment": "coins"}, key)
    action, target, data = "order", "chicken", {"payment": "coins"}
    if difference == "action":
        action = "cook"
    elif difference == "target":
        target = "beef"
    else:
        data = {"payment": "coins", "quantity": 4}
    with pytest.raises(ValueError, match="Chave já utilizada para outra operação"):
        await execute(db, "A", action, target, data, key)
    assert (
        await execute(db, "A", "order", "chicken", {"payment": "coins"}, key)
        == original
    )
    other = await execute(db, "B", "order", "chicken", {"payment": "coins"}, key)
    assert other != original
    async with db.factory() as session:
        for who in ("A", "B"):
            assert (await session.get(HabitatWallet, uid(who))).balance == 88
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(QuintalTask)
                    .where(QuintalTask.user_id == uid(who))
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(HabitatTransaction)
                    .where(
                        HabitatTransaction.user_id == uid(who),
                        HabitatTransaction.event_key == "quintal:" + str(key),
                    )
                )
                == 1
            )
    evidence(
        "DATA04-fingerprint-" + difference,
        dict(
            changed=difference,
            rejected_for_fingerprint=True,
            extra_effects=0,
            same_key_independent_between_accounts=True,
        ),
    )
