"""Cat care, preferences and bowl placement rules; no commits."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.persistence_models import HabitatItem, RemoteCat
from app.quintal_models import QuintalCatCare, QuintalPreference
from app.services.habitat_interactions import reachable
from app.services.habitat_service import item_cells, walkable_cells
from app.services.quintal_catalog import RECIPES, SATISFACTION
from app.services.quintal_clock import day, iso, utc
from app.services.quintal_inventory import counter, rows, stock
from app.services.quintal_return import record_reaction


async def cat_care(session, wallet, cat_id):
    row = await session.get(QuintalCatCare, (wallet.user_id, cat_id))
    if row is None:
        old = (wallet.care_state or {}).get("cats", {}).get(str(cat_id), {})
        row = QuintalCatCare(
            user_id=wallet.user_id,
            cat_id=cat_id,
            state={"affinity": old.get("affinity", 0), "bonuses": {}},
        )
        session.add(row)
        await session.flush()
    return row


async def preference(session, user_id, cat_id, recipe, rng):
    row = await session.get(QuintalPreference, (user_id, cat_id, recipe))
    if row is None:
        draw = rng.random()
        row = QuintalPreference(
            user_id=user_id,
            cat_id=cat_id,
            recipe=recipe,
            state={
                "level": 1 if draw < 0.15 else 2 if draw < 0.4 else 3,
                "known": False,
                "streak": 0,
            },
        )
        session.add(row)
        await session.flush()
    return row


def evolve(state, now, rng):
    level = state["level"]
    last = utc(state.get("evaluated_at"))
    if level in (0, 6) or (last and now - last < timedelta(hours=24)):
        return
    state["evaluated_at"] = now.isoformat()
    draw = rng.random()
    if level == 1:
        state["streak"] = state.get("streak", 0) + 1
        risk = (0.03, 0.06, 0.10)[min(2, state["streak"] - 1)]
        if draw < risk:
            state["level"] = 0
        elif draw < risk + 0.07:
            state["level"] = 2
    elif level == 2:
        if draw < 0.12:
            state["level"] = 3
        elif draw < 0.20:
            state["level"] = 1
    elif level == 3:
        if draw < 0.10:
            state["level"] = 4
        elif draw < 0.18:
            state["level"] = 2
    elif level == 4 and draw < 0.05:
        state["level"] = 5
    elif level == 5 and draw < 0.02:
        state["level"] = 6


def bond(state, action, points, now):
    bonuses = deepcopy(state.get("bonuses", {}))
    if bonuses.get(action) == day(now):
        return
    bonuses[action] = day(now)
    state["bonuses"] = bonuses
    state["affinity"] = state.get("affinity", 0) + points


def play_care_bonus(state, now):
    """Current play contribution, before the yard's aggregate satisfaction cap."""
    at = utc(state.get("played_at"))
    if not at or state.get("assisted"):
        return 0.0
    return 20 * max(0, 1 - max(0, (now - at).total_seconds()) / 86400)


async def meals(session, wallet, care, now, rng):
    state = deepcopy(care.state)
    portions = state.get("portions", 0)
    recipe = state.get("recipe", "basic")
    last = utc(state.get("last_meal_at"))
    served = utc(state.get("served_at")) or now
    next_meal = max(last + timedelta(hours=12), served) if last else served
    while portions > 0 and next_meal <= now:
        portions -= 1
        pref = (
            None
            if recipe == "basic"
            else await preference(session, wallet.user_id, care.cat_id, recipe, rng)
        )
        p = deepcopy(pref.state) if pref else {"level": 3}
        previous_level = p["level"]
        p["known"] = True
        if previous_level == 0:
            await counter(session, wallet.user_id, "refusal", 1)
            state["last_reaction"] = {
                "kind": "refused",
                "recipe": recipe,
                "at": iso(next_meal),
                "lost": 1,
                "portions_before": portions + 1,
                "portions_after": portions,
            }
            state["last_reaction"]["event_id"] = record_reaction(
                session, wallet.user_id, care.cat_id, state["last_reaction"]
            )
            if pref:
                pref.state = p
            continue
        state["last_meal_at"] = iso(next_meal)
        state["meal_satisfaction"] = (
            8 if recipe == "basic" else RECIPES[recipe][4]
        ) * SATISFACTION[previous_level]
        bond(state, "meal", 2, next_meal)
        if previous_level == 6:
            bond(state, "favorite", 1, next_meal)
        if state.get("last_recipe") != recipe:
            for other in await rows(session, QuintalPreference, wallet.user_id):
                if other.cat_id == care.cat_id and other.recipe != recipe:
                    other.state = {**other.state, "streak": 0}
        state["last_recipe"] = recipe
        if pref:
            evolve(p, next_meal, rng)
            pref.state = p
            if p["level"] == 6 and previous_level != 6:
                await counter(
                    session, wallet.user_id, "favorite", 1, occurred_at=next_meal
                )
        state["last_reaction"] = {
            "kind": "meal",
            "recipe": recipe,
            "at": iso(next_meal),
            "preference": previous_level,
            "next_preference": p["level"],
            "portions_before": portions + 1,
            "portions_after": portions,
        }
        state["last_reaction"]["event_id"] = record_reaction(
            session, wallet.user_id, care.cat_id, state["last_reaction"]
        )
        await counter(session, wallet.user_id, "meal", 1, occurred_at=next_meal)
        await counter(session, wallet.user_id, "care_day:" + day(next_meal), 1)
        next_meal += timedelta(hours=12)
    state["portions"] = portions
    care.state = state


async def ensure_pair(session, wallet, cat_id):
    care = await cat_care(session, wallet, cat_id)
    state = deepcopy(care.state)
    used = {
        c.state.get("color_index")
        for c in await rows(session, QuintalCatCare, wallet.user_id)
        if c.cat_id != cat_id and str(c.cat_id) in (wallet.selected_cat_ids or [])
    }
    if state.get("color_index") is None or state["color_index"] in used:
        state["color_index"] = next(
            (index for index in range(6) if index not in used), 0
        )
    items = await rows(session, HabitatItem, wallet.user_id)
    other_refs = {
        c.state.get(role)
        for c in await rows(session, QuintalCatCare, wallet.user_id)
        if c.cat_id != cat_id
        for role in ("food_bowl", "water_bowl")
    }
    for role, sku in (("food_bowl", "food_bowl"), ("water_bowl", "water_bowl")):
        item = next(
            (
                i
                for i in items
                if str(i.id) == state.get(role)
                and i.sku == sku
                and str(i.id) not in other_refs
            ),
            None,
        )
        if item is None:
            item = next(
                (
                    i
                    for i in items
                    if i.sku == sku
                    and str(i.id) not in other_refs
                    and (
                        (i.care_state or {}).get("cat_id") == str(cat_id)
                        or i.purchase_key == f"pair:{cat_id}:{role[:4]}"
                    )
                ),
                None,
            )
        if item is None:
            item = next(
                (
                    i
                    for i in items
                    if i.sku == sku
                    and str(i.id) not in other_refs
                    and not (i.care_state or {}).get("cat_id")
                ),
                None,
            )
        if item is not None:
            state[role] = str(item.id)
            if item.x is None or item.y is None:
                state["assisted"] = str(cat_id) in (wallet.selected_cat_ids or [])
        if item is None:
            item = HabitatItem(
                id=uuid4(),
                user_id=wallet.user_id,
                sku=sku,
                purchase_key=f"pair:{cat_id}:{role[:4]}",
                care_state={"cat_id": str(cat_id), "role": role},
            )
            session.add(item)
            state[role] = str(item.id)
            state["assisted"] = str(cat_id) in (wallet.selected_cat_ids or [])
        item.care_state = {
            **(item.care_state or {}),
            "cat_id": str(cat_id),
            "role": role,
            "color_index": state["color_index"],
        }
    care.state = state
    await session.flush()
    return care


async def validate_bowls(session, wallet, items):
    free = walkable_cells(items, wallet.expansion_level)
    for care in await rows(session, QuintalCatCare, wallet.user_id):
        if str(care.cat_id) not in (wallet.selected_cat_ids or []):
            s = deepcopy(care.state)
            if s.get("portions", 0) and s.get("recipe") != "basic":
                await stock(
                    session, wallet.user_id, {"ration:" + s["recipe"]: s["portions"]}
                )
            s["portions"] = 0
            care.state = s
            for item in items:
                if str(item.id) in (s.get("food_bowl"), s.get("water_bowl")):
                    item.x, item.y = None, None
            continue
        anchor = (wallet.cat_anchors or {}).get(str(care.cat_id))
        if care.state.get("assisted"):
            continue
        for role in ("food_bowl", "water_bowl"):
            item = next((i for i in items if str(i.id) == care.state.get(role)), None)
            if not item or item.x is None or not anchor:
                raise ValueError(
                    "Cada gato precisa dos dois potes posicionados. Reponha o pote ou retire o gato junto."
                )
            goals = {
                (x + dx, y + dy)
                for x, y in item_cells(item.sku, item.x, item.y, item.rotation)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            } & free
            if not reachable(tuple(anchor), goals, free, items):
                raise ValueError("Deixe um caminho livre entre o gato e seus potes.")


async def owned_cat(session, wallet, target):
    try:
        cat_id = UUID(target)
    except (ValueError, TypeError) as error:
        raise ValueError("Gato inválido.") from error
    cat = await session.scalar(
        select(RemoteCat).where(
            RemoteCat.user_id == wallet.user_id,
            RemoteCat.client_id == cat_id,
            RemoteCat.deleted_at.is_(None),
        )
    )
    if (
        not cat
        or not (cat.payload or {}).get("is_owned")
        or target not in (wallet.selected_cat_ids or [])
    ):
        raise ValueError("Escolha um gato seu que esteja no Quintal.")
    return cat_id
