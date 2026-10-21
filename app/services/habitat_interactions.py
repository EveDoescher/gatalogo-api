"""Server-clock care actions; no client animation or timer can mint rewards."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.habitat_pricing import effective_price
from app.persistence_models import HabitatItem, HabitatTransaction, RemoteCat
from app.services.habitat_service import can_cross_bridge, item_cells, walkable_cells, wallet_for

TREAT_PRICE = 4
AFFINITY_GIFT_THRESHOLD = 20
GROWING_SKUS = {"leaf_garden", "bamboo_garden"}
CALENDAR = ZoneInfo("America/Sao_Paulo")

def migrate_calendar(state, now):
    if state.get("calendar_version") == 1: return state
    state = deepcopy(state)
    today, old_today = now.astimezone(CALENDAR).date().isoformat(), now.date().isoformat()
    for cat in state.get("cats", {}).values():
        for action in ("pet", "feed", "treat"):
            if cat.get(action + "_day") in {today, old_today}: cat[action + "_day"] = today
    for field in ("food_day", "gift_day"):
        if state.get(field) in {today, old_today}: state[field] = today
    state["calendar_version"] = 1
    return state


def care_summary(state: dict, capacity: int, now: datetime) -> dict:
    day = now.astimezone(CALENDAR).date().isoformat()
    used = state.get("food_used", 0) if state.get("food_day") == day else 0
    return {"cats": state.get("cats", {}), "free_portions": max(0, capacity - used) + state.get("bonus_portions", 0), "bonus_portions": state.get("bonus_portions", 0),
            "treat_price": effective_price(TREAT_PRICE), "day": day, "server_now": now.isoformat(),
            "gift_threshold": AFFINITY_GIFT_THRESHOLD}


def reachable(start: tuple[int, int], goals: set[tuple[int, int]], free: set[tuple[int, int]], items: list[HabitatItem]) -> bool:
    pending, seen = [start], {start}
    while pending:
        cell = pending.pop()
        if cell in goals:
            return True
        for neighbor in ((cell[0]+1, cell[1]), (cell[0]-1, cell[1]), (cell[0], cell[1]+1), (cell[0], cell[1]-1)):
            if neighbor in free and neighbor not in seen and can_cross_bridge(cell, neighbor, items):
                seen.add(neighbor)
                pending.append(neighbor)
    return False


async def interact(session: AsyncSession, user_id: UUID, action: str, target_id: UUID,
                   idempotency_key: UUID, bowl_id: UUID | None = None,
                   *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    wallet = await wallet_for(session, user_id)
    key = f"interaction:{idempotency_key}"
    details = {"action": action, "target_id": str(target_id), "bowl_id": str(bowl_id) if bowl_id else None}
    previous = await session.scalar(select(HabitatTransaction).where(
        HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == key))
    if previous is not None:
        if previous.details != details:
            raise ValueError("Esta ação já foi usada com outro destino.")
        return
    items = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user_id))).scalars().all()
    state = migrate_calendar(deepcopy(wallet.care_state or {}), now)
    day = now.astimezone(CALENDAR).date().isoformat()
    amount = 0
    if action == "water":
        plant = next((item for item in items if item.id == target_id and item.sku in GROWING_SKUS and item.x is not None), None)
        if plant is None:
            raise ValueError("Coloque um canteiro seu no quintal para regar.")
        plant_state = deepcopy(plant.care_state or {})
        if plant_state.get("grow_at") and datetime.fromisoformat(plant_state["grow_at"]) <= now:
            raise ValueError("Esta planta já cresceu.")
        last_water = plant_state.get("last_watered_at")
        if last_water and now - datetime.fromisoformat(last_water) < timedelta(hours=6):
            raise ValueError("Espere seis horas entre as regas.")
        plant_state["grow_at"] = (now if last_water else now + timedelta(hours=12)).isoformat()
        plant_state["last_watered_at"] = now.isoformat()
        plant.care_state = plant_state
    elif action in ("pet", "feed", "treat"):
        upgraded = state.get("production_cats", {}).get(str(target_id))
        if action in ("feed", "treat") and upgraded and not upgraded.get("assisted"):
            raise ValueError("Alimente este gato pelo pote individual de ração.")
        cat = await session.scalar(select(RemoteCat).where(
            RemoteCat.user_id == user_id, RemoteCat.client_id == target_id, RemoteCat.deleted_at.is_(None)))
        if cat is None or not (cat.payload or {}).get("is_owned") or str(target_id) not in (wallet.selected_cat_ids or []):
            raise ValueError("Escolha um dos seus gatos que estão no Quintal.")
        cats = state.setdefault("cats", {})
        cat_state = cats.setdefault(str(target_id), {"affinity": 0})
        if cat_state.get(f"{action}_day") == day:
            raise ValueError("Este cuidado já foi registrado para este gato hoje.")
        if action in ("feed", "treat"):
            bowl = next((item for item in items if item.id == bowl_id and item.sku == "food_bowl" and item.x is not None), None)
            if bowl is None:
                raise ValueError("Coloque um potinho no quintal primeiro.")
            footprint = item_cells(bowl.sku, bowl.x, bowl.y, bowl.rotation)
            free = walkable_cells(items, wallet.expansion_level)
            access = {(x+dx, y+dy) for x, y in footprint for dx, dy in ((1,0),(-1,0),(0,1),(0,-1))} & free
            anchor = (wallet.cat_anchors or {}).get(str(target_id))
            if not anchor or not reachable(tuple(anchor), access, free, items):
                raise ValueError("Deixe um caminho livre entre o gato e o potinho.")
            if action == "feed":
                if state.get("food_day") != day:
                    state.update(food_day=day, food_used=0)
                if state.get("food_used", 0) >= 3 + wallet.expansion_level and state.get("bonus_portions", 0) <= 0:
                    raise ValueError("As porções gratuitas de hoje acabaram.")
                if state.get("food_used", 0) < 3 + wallet.expansion_level:
                    state["food_used"] = state.get("food_used", 0) + 1
                else:
                    state["bonus_portions"] -= 1
            else:
                if wallet.balance < effective_price(TREAT_PRICE):
                    raise ValueError("Moedas insuficientes para o petisco.")
                amount = -effective_price(TREAT_PRICE)
        cat_state[f"{action}_day"] = day
        if action == "pet":
            # Migrate before changing the legacy affinity, so the first care
            # and its replay cannot become two distinct production bonuses.
            from app.services.quintal_care import cat_care, bond
            from app.services.quintal_simulation import reconcile
            care = await cat_care(session, wallet, target_id)
            production_state = deepcopy(care.state)
            production_state.setdefault("assisted", not bool(production_state.get("food_bowl") and production_state.get("water_bowl")))
            bond(production_state, "pet", 1, now)
            care.state = production_state
            cat_state["affinity"] = production_state["affinity"]
        else:
            cat_state["affinity"] = min(100, cat_state.get("affinity", 0) + {"feed": 5, "treat": 3}[action])
        if cat_state["affinity"] >= AFFINITY_GIFT_THRESHOLD and not state.get("gift_claimed"):
            state["gift_claimed"] = True
            session.add(HabitatItem(user_id=user_id, sku="friendship_garden"))
            from app.services.quintal_rewards import record_bond_reward
            await record_bond_reward(session, user_id, target_id, AFFINITY_GIFT_THRESHOLD, "friendship_garden")
        wallet.care_state = state
        if action == "pet":
            await reconcile(session, wallet, now)
    else:
        raise ValueError("Cuidado desconhecido.")
    wallet.balance += amount
    session.add(HabitatTransaction(user_id=user_id, event_key=key, category=action,
                                   amount=amount, details=details))
    await session.flush()
