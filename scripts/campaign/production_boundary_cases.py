"""Exact server-clock, balance, inventory and concurrent collection boundaries."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from app.persistence_models import HabitatWallet, HabitatTransaction
from app.quintal_models import QuintalResource, QuintalTask
from app.services import progression
from app.services.quintal_production import operation


async def execute(db, action, target, key, data=None, clock=NOW):
    async with db.factory() as session:
        await progression.account(session, uid("A"))
        result = await operation(
            session, uid("A"), action, target, data or {}, key, now=clock
        )
        await session.commit()
        return result


async def ready_task(db):
    async with db.factory() as session:
        session.add(
            QuintalTask(
                id=uid("deadline-task"),
                user_id=uid("A"),
                kind="order",
                target="chicken",
                status="active",
                started_at=NOW - timedelta(hours=1),
                ready_at=NOW,
                inputs={},
                outputs={"chicken": 2},
            )
        )
        await session.commit()
    return uid("deadline-task")


@pytest.mark.asyncio
@pytest.mark.parametrize("milliseconds", [-1, 0, 1])
async def test_DATA07_exact_collection_deadline(db, milliseconds):
    task = await ready_task(db)
    clock = NOW + timedelta(milliseconds=milliseconds)
    if milliseconds < 0:
        with pytest.raises(ValueError, match="pronta"):
            await execute(db, "collect", str(task), uid("deadline-intent"), clock=clock)
    else:
        first = await execute(
            db, "collect", str(task), uid("deadline-intent"), clock=clock
        )
        assert (
            await execute(db, "collect", str(task), uid("deadline-intent"), clock=clock)
            == first
        )
    async with db.factory() as session:
        resource = await session.get(QuintalResource, (uid("A"), "chicken"))
        assert (resource.quantity if resource else 0) == (0 if milliseconds < 0 else 2)
        assert (await session.get(QuintalTask, task)).status == (
            "active" if milliseconds < 0 else "collected"
        )
    evidence(
        "DATA07-deadline-" + str(milliseconds),
        dict(
            offset_ms=milliseconds,
            accepted=milliseconds >= 0,
            replay_does_not_duplicate=True,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("balance", [0, 11, 12, 13])
async def test_DATA07_exact_balance_and_no_partial_debit(db, balance):
    async with db.factory() as session:
        (await session.get(HabitatWallet, uid("A"))).balance = balance
        await session.commit()
    if balance < 12:
        with pytest.raises(ValueError, match="Moedas insuficientes"):
            await execute(
                db, "order", "chicken", uid("balance-intent"), {"payment": "coins"}
            )
    else:
        await execute(
            db, "order", "chicken", uid("balance-intent"), {"payment": "coins"}
        )
    async with db.factory() as session:
        assert (await session.get(HabitatWallet, uid("A"))).balance == (
            balance if balance < 12 else balance - 12
        )
        assert await session.scalar(
            select(func.count()).select_from(QuintalTask)
        ) == int(balance >= 12)
        count = await session.scalar(
            select(func.count())
            .select_from(HabitatTransaction)
            .where(
                HabitatTransaction.event_key == "quintal:" + str(uid("balance-intent"))
            )
        )
        assert count == int(balance >= 12)
    evidence(
        "DATA07-balance-" + str(balance),
        dict(initial=balance, cost=12, accepted=balance >= 12, partial_debit=False),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("corn,wheat", [(0, 0), (1, 1), (2, 0), (2, 1), (3, 2)])
async def test_DATA07_product_exchange_minimum_inventory(db, corn, wheat):
    async with db.factory() as session:
        session.add_all(
            [
                QuintalResource(user_id=uid("A"), resource=k, quantity=v)
                for k, v in (("corn", corn), ("wheat", wheat))
            ]
        )
        await session.commit()
    accepted = corn >= 2 and wheat >= 1
    if accepted:
        await execute(
            db, "order", "chicken", uid("stock-intent"), {"payment": "products"}
        )
    else:
        with pytest.raises(ValueError, match="insuficientes"):
            await execute(
                db, "order", "chicken", uid("stock-intent"), {"payment": "products"}
            )
    async with db.factory() as session:
        for key, old, cost in (("corn", corn, 2), ("wheat", wheat, 1)):
            assert (
                await session.get(QuintalResource, (uid("A"), key))
            ).quantity == old - (cost if accepted else 0)
        assert (await session.get(HabitatWallet, uid("A"))).balance == 100
        assert await session.scalar(
            select(func.count()).select_from(QuintalTask)
        ) == int(accepted)
    evidence(
        f"DATA07-stock-{corn}-{wheat}",
        dict(
            corn=corn,
            wheat=wheat,
            accepted=accepted,
            no_negative_or_partial_inventory=True,
        ),
    )


@pytest.mark.asyncio
async def test_DATA07_double_collect_different_intents_is_single_reward(db):
    task = await ready_task(db)
    barrier = asyncio.Event()

    async def call(label):
        await barrier.wait()
        try:
            await execute(db, "collect", str(task), uid(label))
            return "accepted"
        except ValueError as error:
            assert "coletada" in str(error)
            return "rejected"

    jobs = [asyncio.create_task(call(name)) for name in ("collect-one", "collect-two")]
    barrier.set()
    assert sorted(await asyncio.gather(*jobs)) == ["accepted", "rejected"]
    async with db.factory() as session:
        assert (await session.get(QuintalResource, (uid("A"), "chicken"))).quantity == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(HabitatTransaction)
                .where(HabitatTransaction.category == "quintal")
            )
            == 1
        )
    evidence(
        "DATA07-double-collect",
        dict(
            concurrent_intents=2,
            accepted=1,
            rejected=1,
            rewarded_quantity=2,
            receipts=1,
        ),
    )


@pytest.mark.asyncio
async def test_DATA07_http_future_client_clock_cannot_collect_early(db):
    from datetime import UTC, datetime

    task = await ready_task(db)
    async with db.factory() as session:
        current = await session.get(QuintalTask, task)
        current.started_at = datetime.now(UTC)
        current.ready_at = datetime.now(UTC) + timedelta(days=1)
        await session.commit()
    payload = dict(
        action="collect",
        target=str(task),
        data={},
        idempotency_key=str(uid("future-client")),
        now="2099-01-01T00:00:00Z",
        ready_at="2000-01-01T00:00:00Z",
    )
    reply = await db.client.post(
        "/app/quintal/operations", headers=db.headers("A"), json=payload
    )
    assert reply.status_code == 409
    payload["data"] = {"now": "2099-01-01T00:00:00Z"}
    assert (
        await db.client.post(
            "/app/quintal/operations", headers=db.headers("A"), json=payload
        )
    ).status_code == 422
    async with db.factory() as session:
        assert (await session.get(QuintalTask, task)).status == "active"
        assert await session.get(QuintalResource, (uid("A"), "chicken")) is None
    evidence(
        "DATA07-client-clock",
        dict(
            top_level_forged_clock=409,
            unrecognized_data_clock=422,
            task_still_active=True,
            reward_quantity=0,
        ),
    )
