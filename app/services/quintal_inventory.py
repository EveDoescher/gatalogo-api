"""Inventory and counters; callers own the transaction and wallet lock."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.persistence_models import HabitatTransaction
from app.quintal_models import (
    QuintalCounter,
    QuintalFacility,
    QuintalResource,
    QuintalTask,
)
from app.services.quintal_clock import iso

PROGRESS_METRICS = {
    "cat_set",
    "water_cat",
    "plant",
    "water_crop",
    "harvest",
    "cook",
    "meal",
    "play",
    "fish",
    "egg",
    "milk_collection",
    "facility",
    "favorite",
    "bond",
}


async def rows(session, model, user_id):
    query = select(model).where(model.user_id == user_id)
    if model is QuintalTask:
        query = query.where(model.status.in_(("active", "ready")))
    return list((await session.scalars(query)).all())


async def counter(session, user_id, metric, amount=0, *, occurred_at=None):
    row = await session.get(QuintalCounter, (user_id, metric))
    if row is None:
        row = QuintalCounter(
            user_id=user_id, metric=metric, quantity=0, projected_quantity=0
        )
        session.add(row)
    row.quantity += amount
    if amount > 0 and metric in PROGRESS_METRICS:
        session.add(
            HabitatTransaction(
                user_id=user_id,
                event_key=f"qactivity:{metric}:{row.quantity}",
                category="quintal_activity",
                amount=0,
                details={
                    "metric": metric,
                    "quantity": amount,
                    "value": row.quantity,
                    "occurred_at": iso(occurred_at or datetime.now(UTC)),
                    "projected": False,
                },
            )
        )
    await session.flush()
    return row.quantity


async def stock(session, user_id, changes):
    owned = {r.resource: r for r in await rows(session, QuintalResource, user_id)}
    if any(
        (owned[k].quantity if k in owned else 0) + n < 0 for k, n in changes.items()
    ):
        raise ValueError("Ingredientes ou porções insuficientes.")
    for key, amount in changes.items():
        if key not in owned:
            owned[key] = QuintalResource(user_id=user_id, resource=key, quantity=0)
            session.add(owned[key])
        owned[key].quantity += amount
    await session.flush()


async def levels(session, user_id):
    return {f.kind: f.level for f in await rows(session, QuintalFacility, user_id)}


async def available_requirements(session, wallet):
    values = {
        r.metric: r.quantity
        for r in await rows(session, QuintalCounter, wallet.user_id)
    }
    values.update(
        {"facility:" + k: v for k, v in (await levels(session, wallet.user_id)).items()}
    )
    legacy = (wallet.care_state or {}).get("cats", {})
    values["care"] = max(
        values.get("care", 0),
        int(any(c.get("pet_day") or c.get("feed_day") for c in legacy.values())),
    )
    return values
