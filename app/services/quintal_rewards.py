"""Presentation receipts for rewards already granted in the wallet transaction."""
from sqlalchemy import select
from app.persistence_models import HabitatTransaction
from app.services.habitat_service import SHOP


async def record(session, user_id, key, details):
    existing = await session.scalar(select(HabitatTransaction.id).where(
        HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == key))
    if existing is None:
        session.add(HabitatTransaction(user_id=user_id, event_key=key,
            category="quintal_reward", amount=0, details=details))
        await session.flush()


async def record_progress_rewards(session, event):
    result = event.result or {}
    bonuses = result.get("bonuses", [])
    items = result.get("unlocked_items", [])
    if not bonuses and not items:
        return  # Routine care/production does not open a celebration.
    await record(session, event.user_id, f"qreward:progress:{event.id}", {
        "kind": "progress", "title": "Conquista no Quintal",
        "bonuses": bonuses, "items": items,
        "cat_id": event.record_id if event.kind == "care" else None,
        "metric": event.evidence.get("metric"),
    })


async def record_bond_reward(session, user_id, cat_id, threshold, sku):
    await record(session, user_id, f"qreward:bond:{cat_id}:{threshold}", {
        "kind": "bond", "title": "A amizade floresceu!", "cat_id": str(cat_id),
        "threshold": threshold, "bonuses": [],
        "items": [{"sku": sku, "name": SHOP[sku]["name"], "quantity": 1}],
    })


async def pending_rewards(session, user_id):
    receipts = (await session.scalars(select(HabitatTransaction).where(
        HabitatTransaction.user_id == user_id,
        HabitatTransaction.category == "quintal_reward",
        HabitatTransaction.details["acknowledged"].as_boolean().is_not(True),
    ).order_by(HabitatTransaction.created_at, HabitatTransaction.id).limit(100))).all()
    return [{**receipt.details, "id": receipt.event_key} for receipt in receipts]


async def acknowledge_rewards(session, user_id, event_ids):
    if not event_ids:
        return 0
    receipts = (await session.scalars(select(HabitatTransaction).where(
        HabitatTransaction.user_id == user_id,
        HabitatTransaction.category == "quintal_reward",
        HabitatTransaction.event_key.in_(event_ids),
    ))).all()
    for receipt in receipts:
        receipt.details = {**receipt.details, "acknowledged": True}
    return len(receipts)
