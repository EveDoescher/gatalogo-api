"""Reconcile and present authoritative production state; no commits."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.quintal_models import (
    QuintalCatCare,
    QuintalFacility,
    QuintalPlot,
    QuintalPreference,
    QuintalResident,
    QuintalResource,
    QuintalTask,
)
from app.services.habitat_service import wallet_for
from app.services.quintal_care import ensure_pair
from app.services.quintal_catalog import CROPS, FACILITIES, catalog
from app.services.quintal_clock import day, iso, utc
from app.services.quintal_inventory import available_requirements, counter, rows
from app.services.quintal_return import visible_updates
from app.services.quintal_simulation import reconcile


def resident_projection(resident, now):
    """Describe the next actionable state from reconciled, bounded production."""
    state = deepcopy(resident.state)
    orchard = resident.kind == "orchard"
    cycle = 21600 if resident.kind == "coop" else 28800
    capacity = 3 if orchard else 2
    next_times = []
    for animal in state.get("animals", []):
        animal["next_ready_at"] = None
        animal["capacity"] = capacity
        food_until = utc(animal.get("food_until")) or now
        water_until = utc(state.get("water_until")) or now
        full = animal.get("ready", 0) >= capacity
        pause = (
            "full"
            if full
            else "needs_food"
            if not orchard and food_until <= now
            else "needs_water"
            if not orchard and water_until <= now
            else None
        )
        animal["production_progress"] = {
            "observed_at": iso(now),
            "cycle_seconds": cycle,
            "elapsed_seconds": min(cycle, max(0, animal.get("progress", 0))),
            "pause_reason": pause,
            "active_until": None if orchard else iso(min(food_until, water_until)),
            "expires_reason": "needs_food"
            if food_until <= water_until
            else "needs_water",
        }
        if animal.get("ready", 0) >= capacity:
            continue
        if not orchard:
            available_until = min(
                utc(animal.get("food_until")) or now,
                utc(state.get("water_until")) or now,
            )
            if available_until <= now:
                continue
        remaining = max(0, cycle - animal.get("progress", 0))
        expected = now + timedelta(seconds=remaining)
        if orchard or expected <= available_until:
            next_times.append(expected)
            animal["next_ready_at"] = iso(expected)
    count = state.get("count", 0)
    status = (
        "full"
        if state.get("ready", 0) >= count * capacity
        else "needs_food"
        if not orchard and state.get("fed_count", 0) < count
        else "needs_water"
        if not orchard and (utc(state.get("water_until")) or now) <= now
        else "producing"
    )
    progress_candidates = [
        a["production_progress"]
        for a in state.get("animals", [])
        if a.get("ready", 0) < capacity
    ]
    progress_candidates.sort(
        key=lambda item: (item["pause_reason"] is not None, -item["elapsed_seconds"])
    )
    return {
        "kind": resident.kind,
        **state,
        "capacity": count * capacity,
        "production_progress": progress_candidates[0] if progress_candidates else None,
        "production_status": status,
        "next_ready_at": iso(min(next_times)) if next_times else None,
    }


async def projection(session, user_id, now=None):
    now = now or datetime.now(UTC)
    wallet = await wallet_for(session, user_id)
    for raw in wallet.selected_cat_ids or []:
        try:
            cat_id = UUID(raw)
        except ValueError:
            continue
        await ensure_pair(session, wallet, cat_id)
    await reconcile(session, wallet, now)
    requirements = await available_requirements(session, wallet)
    facilities = await rows(session, QuintalFacility, user_id)
    existing = {f.kind: f for f in facilities}
    offers = []
    for key, definition in FACILITIES.items():
        kind, level = key.split(":")
        f = existing.get(kind)
        if int(level) != (f.level if f else 0) + 1:
            continue
        reasons = []
        if wallet.expansion_level < definition[3]:
            reasons.append(f"Quintal expansão {definition[3]}")
        labels = {
            "care": "Cuidados com os gatos",
            "harvest": "Colheitas",
            "cook": "Lotes preparados",
            "fish": "Capturas",
            "egg": "Ovos coletados",
            "milk_collection": "Coletas de leite",
            "crop:corn": "Colheitas de milho",
            "facility:kitchen": "Nível da cozinha",
            "facility:coop": "Nível do galinheiro",
            "facility:garden": "Nível da horta",
            "facility:pier": "Nível do píer",
        }
        for metric, needed in definition[4].items():
            if requirements.get(metric, 0) < needed:
                reasons.append(
                    f"{labels.get(metric, metric)}: {requirements.get(metric, 0)}/{needed}"
                )
        offers.append(
            {
                "id": key,
                "available": not reasons and not (f and f.target_level),
                "reasons": reasons,
            }
        )
    seen = await counter(session, user_id, "summary_seen")
    updates = []
    for task in await rows(session, QuintalTask, user_id):
        if task.status == "ready" and utc(task.ready_at).timestamp() > seen:
            updates.append(
                {
                    "_source": f"task:{task.id}",
                    "kind": task.kind,
                    "target": task.target,
                    "id": str(task.id),
                    "outputs": task.outputs,
                }
            )
    for plot in await rows(session, QuintalPlot, user_id):
        if (
            plot.crop
            and utc(plot.ready_at) <= now
            and utc(plot.ready_at).timestamp() > seen
        ):
            updates.append(
                {
                    "_source": f"plot:{plot.slot}:{iso(plot.started_at)}",
                    "kind": "harvest",
                    "target": plot.crop,
                    "slot": plot.slot,
                    "quantity": CROPS[plot.crop][3],
                }
            )
    for resident in await rows(session, QuintalResident, user_id):
        if (
            resident.state.get("ready", 0)
            and utc(resident.state.get("ready_since"))
            and utc(resident.state["ready_since"]).timestamp() > seen
        ):
            updates.append(
                {
                    "_source": f"resident:{resident.kind}:{resident.state['ready_since']}",
                    "kind": "resident",
                    "target": resident.kind,
                    "quantity": resident.state["ready"],
                }
            )
    for care in await rows(session, QuintalCatCare, user_id):
        reaction = care.state.get("last_reaction", {})
        if (
            not reaction.get("event_id")
            and utc(reaction.get("at"))
            and utc(reaction["at"]).timestamp() > seen
        ):
            updates.append(
                {
                    "_source": f"reaction:{care.cat_id}:{reaction['at']}",
                    "kind": reaction["kind"],
                    "target": str(care.cat_id),
                    "recipe": reaction["recipe"],
                }
            )
    for facility in facilities:
        if facility.target_level and utc(facility.ready_at) <= now:
            updates.append(
                {
                    "_source": f"build:{facility.kind}:{facility.target_level}:{iso(facility.ready_at)}",
                    "kind": "build",
                    "target": facility.kind,
                    "level": facility.target_level,
                }
            )
    updates = await visible_updates(session, user_id, updates)
    return {
        "server_now": iso(now),
        "calendar_day": day(now),
        "revision": wallet.layout_revision,
        "catalog": catalog(),
        "offers": offers,
        "counters": requirements,
        "updates": updates,
        "stock": {
            r.resource: r.quantity
            for r in await rows(session, QuintalResource, user_id)
        },
        "facilities": [
            {
                "kind": f.kind,
                "level": f.level,
                "target_level": f.target_level,
                "started_at": iso(f.started_at),
                "ready_at": iso(f.ready_at),
            }
            for f in facilities
        ],
        "plots": [
            {
                "slot": p.slot,
                "crop": p.crop,
                "started_at": iso(p.started_at),
                "ready_at": iso(p.ready_at),
                "watered": bool(p.watered),
            }
            for p in sorted(
                await rows(session, QuintalPlot, user_id), key=lambda p: p.slot
            )
        ],
        "tasks": [
            {
                "id": str(t.id),
                "kind": t.kind,
                "target": t.target,
                "status": t.status,
                "started_at": iso(t.started_at),
                "ready_at": iso(t.ready_at),
                "inputs": t.inputs,
                "outputs": t.outputs,
            }
            for t in await rows(session, QuintalTask, user_id)
            if t.status in ("active", "ready")
        ],
        "residents": [
            resident_projection(r, now)
            for r in await rows(session, QuintalResident, user_id)
        ],
        "cats": [
            {
                "cat_id": str(c.cat_id),
                **c.state,
                "next_appetite_at": iso(
                    utc(c.state["last_meal_at"]) + timedelta(hours=12)
                )
                if c.state.get("last_meal_at")
                else None,
                "hunger": min(
                    1.0,
                    max(
                        0.0,
                        (now - utc(c.state["last_meal_at"])).total_seconds() / 43200,
                    ),
                )
                if c.state.get("last_meal_at")
                else 1.0,
                "water_available": bool(
                    c.state.get("water_until") and utc(c.state["water_until"]) > now
                ),
            }
            for c in await rows(session, QuintalCatCare, user_id)
        ],
        "preferences": [
            {"cat_id": str(p.cat_id), "recipe": p.recipe, **p.state}
            for p in await rows(session, QuintalPreference, user_id)
            if p.state.get("known")
        ],
    }
