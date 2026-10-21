"""Advance server-clock production within the caller transaction."""

import random
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.persistence_models import HabitatItem, HabitatTransaction
from app.quintal_models import QuintalCatCare, QuintalResident, QuintalTask
from app.services.quintal_care import meals
from app.services.quintal_catalog import BOND_REWARDS
from app.services.quintal_clock import iso, utc
from app.services.quintal_inventory import counter, rows
from app.services.quintal_rewards import record_bond_reward


def resident_animals(state, now):
    animals = deepcopy(state.get("animals", []))
    while len(animals) < state.get("count", 0):
        slot = len(animals)
        animal = {
            "id": str(uuid4()),
            "slot": slot,
            "cursor": state.get("cursor") or iso(now),
            "progress": 0,
            "ready": 0,
        }
        # Only legacy aggregate rows inherit their existing supplies; newly
        # constructed residents are added explicitly with empty food storage.
        if "animals" not in state:
            for key in ("food_start", "food_until"):
                if state.get(key):
                    animal[key] = state[key]
        animals.append(animal)
    return animals


async def reconcile(session, wallet, now=None, rng=None):
    now, rng = now or datetime.now(UTC), rng or random.SystemRandom()
    for task in await rows(session, QuintalTask, wallet.user_id):
        if task.status == "active" and utc(task.ready_at) <= now:
            task.status = "ready"
            if task.kind == "cook":
                await counter(
                    session, wallet.user_id, "cook", 1, occurred_at=utc(task.ready_at)
                )
    for resident in await rows(session, QuintalResident, wallet.user_id):
        s = deepcopy(resident.state)
        animals = resident_animals(s, now)
        previous_amount = sum(a.get("ready", 0) for a in animals)
        cycle, capacity = (
            (21600, 2)
            if resident.kind == "coop"
            else (28800, 3)
            if resident.kind == "orchard"
            else (28800, 2)
        )
        for a in animals:
            cursor = utc(a.get("cursor")) or now
            start = max(
                cursor,
                utc(a.get("food_start")) or now,
                utc(s.get("water_start")) or now,
            )
            end = min(
                now,
                utc(a.get("food_until")) or cursor,
                utc(s.get("water_until")) or cursor,
            )
            if resident.kind == "orchard":
                start, end = cursor, now
            ready, progress = a.get("ready", 0), a.get("progress", 0)
            if ready < capacity and end > start:
                progress += (end - start).total_seconds()
                per_cycle = 3 if resident.kind == "orchard" else 1
                ready += min(capacity - ready, int(progress // cycle) * per_cycle)
                progress = 0 if ready >= capacity else progress % cycle
            a.update(ready=ready, progress=progress, cursor=iso(now))
        amount = sum(a["ready"] for a in animals)
        s.update(ready=amount, animals=animals, cursor=iso(now))
        if amount > previous_amount:
            s["ready_since"] = iso(now)
        fed = [
            a
            for a in animals
            if utc(a.get("food_until")) and utc(a["food_until"]) > now
        ]
        s["fed_count"] = len(fed)
        s["food_until"] = (
            min((a["food_until"] for a in fed), default=None)
            if len(fed) == len(animals)
            else None
        )
        resident.state = s
    for care in await rows(session, QuintalCatCare, wallet.user_id):
        if str(care.cat_id) in (wallet.selected_cat_ids or []):
            await meals(session, wallet, care, now, rng)
    care_state = deepcopy(wallet.care_state or {})
    legacy_cats = care_state.setdefault("cats", {})
    for care in await rows(session, QuintalCatCare, wallet.user_id):
        old_affinity = legacy_cats.get(str(care.cat_id), {}).get("affinity", 0)
        affinity = care.state.get("affinity", 0)
        for threshold, sku in BOND_REWARDS:
            if affinity < threshold:
                continue
            key = f"bond:{care.cat_id}:{threshold}"
            exists = await session.scalar(
                select(HabitatTransaction.id).where(
                    HabitatTransaction.user_id == wallet.user_id,
                    HabitatTransaction.event_key == key,
                )
            )
            if exists:
                continue
            preserved = (
                threshold == 20
                and old_affinity >= 20
                and care_state.get("gift_claimed")
            )
            if not preserved:
                session.add(
                    HabitatItem(user_id=wallet.user_id, sku=sku, purchase_key=key)
                )
                await record_bond_reward(
                    session, wallet.user_id, care.cat_id, threshold, sku
                )
            session.add(
                HabitatTransaction(
                    user_id=wallet.user_id,
                    event_key=key,
                    category="bond",
                    amount=0,
                    details={
                        "cat_id": str(care.cat_id),
                        "threshold": threshold,
                        "sku": sku,
                        "preserved": bool(preserved),
                    },
                )
            )
        legacy_cats[str(care.cat_id)] = {
            **legacy_cats.get(str(care.cat_id), {}),
            "affinity": affinity,
        }
    care_state["production_cats"] = {
        str(c.cat_id): {
            k: c.state.get(k)
            for k in (
                "last_meal_at",
                "meal_satisfaction",
                "played_at",
                "water_until",
                "affinity",
                "assisted",
            )
        }
        for c in await rows(session, QuintalCatCare, wallet.user_id)
    }
    wallet.care_state = care_state
    affinity = max(
        (
            c.state.get("affinity", 0)
            for c in await rows(session, QuintalCatCare, wallet.user_id)
        ),
        default=0,
    )
    current = await counter(session, wallet.user_id, "bond")
    if affinity > current:
        await counter(
            session, wallet.user_id, "bond", affinity - current, occurred_at=now
        )
    await session.flush()
