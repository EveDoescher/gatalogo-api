"""Wallet-locked visits and gifts. JSON state avoids replaying or resetting wallets."""
from copy import deepcopy
from datetime import UTC, datetime
import random
from uuid import uuid4
from sqlalchemy import select
from app.config import get_settings
from app.persistence_models import AppNotification, HabitatItem, HabitatTransaction
from app.services.habitat_service import SHOP, item_cells, walkable_cells, wallet_for
from app.services.habitat_interactions import reachable, CALENDAR, migrate_calendar
from app.services.realtime_events import queue_notification


def satisfaction(wallet, items, now):
    settings = get_settings()
    if not wallet.selected_cat_ids:
        return {"value": 0, "comfort": 0, "care": 0}
    free = walkable_cells(items, wallet.expansion_level)
    values, comforts, cares = [], [], []
    for cat in wallet.selected_cat_ids:
        anchor = (wallet.cat_anchors or {}).get(cat)
        comfort = 0
        for category in ("descanso", "brincadeira"):
            for item in items:
                if item.x is None or item.sku in {"food_bowl", "water_bowl"} or SHOP[item.sku]["category"] != category:
                    continue
                footprint = item_cells(item.sku, item.x, item.y, item.rotation)
                access = {(x+dx, y+dy) for x,y in footprint for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))} & free
                if anchor and reachable(tuple(anchor), access, free, items):
                    comfort += settings.habitat_satisfaction_comfort
                    break
        care = (wallet.care_state or {}).get("cats", {}).get(cat, {})
        today = now.astimezone(CALENDAR).date().isoformat()
        recent = settings.habitat_satisfaction_care * sum(care.get(f"{action}_day") == today for action in ("pet", "feed"))
        production = (wallet.care_state or {}).get("production_cats", {}).get(cat)
        if production and not production.get("assisted"):
            from app.services.quintal_clock import utc
            from app.services.quintal_care import play_care_bonus
            def decay(at, value, hours):
                return max(0, 1 - max(0, (now - utc(at)).total_seconds()) / (hours * 3600)) * value if at else 0
            recent = decay(production.get("last_meal_at"), production.get("meal_satisfaction") or 0, 24) + play_care_bonus(production, now)
            recent += 10 if production.get("water_until") and utc(production["water_until"]) > now else 0
            recent += 5 if care.get("pet_day") == today else 0
        comforts.append(comfort); cares.append(recent); values.append(min(100, settings.habitat_satisfaction_base + comfort + recent))
    return {"value": round(sum(values)/len(values)), "comfort": round(sum(comforts)/len(values)), "care": round(sum(cares)/len(values))}


async def visit(session, user_id, *, now=None, rng=None):
    now = now or datetime.now(UTC); rng = rng or random.SystemRandom()
    settings = get_settings()
    wallet = await wallet_for(session, user_id)
    state = migrate_calendar(deepcopy(wallet.care_state or {}), now)
    if state.get("pending_gift"):
        return
    window = int(now.timestamp()) // (settings.habitat_visit_window_minutes * 60)
    if state.get("encounter_window", -1) >= window:
        return
    state["encounter_window"] = window
    day = now.astimezone(CALENDAR).date().isoformat()
    if state.get("gift_day") != day:
        state.update(gift_day=day, gifts_today=0)
    items = list((await session.scalars(select(HabitatItem).where(HabitatItem.user_id == user_id))).all())
    score = satisfaction(wallet, items, now)["value"]
    chance = min(1, settings.habitat_gift_base_chance + settings.habitat_gift_satisfaction_bonus * score / 100)
    if state.get("gifts_today", 0) < settings.habitat_gifts_per_day and rng.random() < chance:
        visitors = ('bee', 'bunny', 'chick', 'fox', 'parrot')
        visitor = visitors[min(int(rng.random() * len(visitors)), len(visitors)-1)]
        state["pending_gift"] = {"id": str(uuid4()), "state": "visiting", "visitor": visitor,
            "created_at": now.isoformat(), "chance": chance, "satisfaction": score,
            "reward": rng.choice([{"coins": 12}, {"portions": 2}, {"sku": "purple_flowers"}])}
        state["gifts_today"] = state.get("gifts_today", 0) + 1
    wallet.care_state = state


async def deliver(session, user_id, gift_id=None, *, now=None, recover=False):
    now = now or datetime.now(UTC)
    wallet = await wallet_for(session, user_id)
    state = deepcopy(wallet.care_state or {})
    gift = state.get("pending_gift")
    if not gift or (gift_id and gift["id"] != str(gift_id)):
        if recover: return
        raise ValueError("Este presente não está disponível nesta conta.")
    if gift["state"] == "available": return
    age = (now - datetime.fromisoformat(gift["created_at"])).total_seconds()
    if age < (60 if recover else 12):
        if recover: return
        raise ValueError("O visitante ainda está chegando.")
    gift["state"] = "available"
    notice = AppNotification(id=uuid4(), user_id=user_id, type="habitat_gift", payload={
        "gift_id": gift["id"], "title": "Um visitante deixou um presente!",
        "body": "Seu presente está do lado de fora do quintal. Toque para encontrá-lo."})
    session.add(notice); queue_notification(session, notice)
    wallet.care_state = state


async def claim(session, user_id, gift_id):
    wallet = await wallet_for(session, user_id)
    event_key = f"gift:{gift_id}"
    previous = await session.scalar(select(HabitatTransaction).where(HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == event_key))
    if previous: return previous.details
    state = deepcopy(wallet.care_state or {})
    gift = state.get("pending_gift")
    if not gift or gift["id"] != str(gift_id) or gift["state"] != "available":
        raise ValueError("Este presente não está disponível para resgate.")
    reward = dict(gift["reward"])
    if reward.get("sku"):
        sku = reward["sku"]
        owned = list((await session.scalars(select(HabitatItem).where(HabitatItem.user_id == user_id, HabitatItem.sku == sku))).all())
        if len(owned) >= SHOP[sku]["max_owned"]: reward = {"coins": 12}
        else: session.add(HabitatItem(id=uuid4(), user_id=user_id, sku=sku, purchase_key=f"gift:{gift_id}"))
    wallet.balance += reward.get("coins", 0)
    state["bonus_portions"] = state.get("bonus_portions", 0) + reward.get("portions", 0)
    state.pop("pending_gift")
    wallet.care_state = state
    session.add(HabitatTransaction(user_id=user_id, event_key=event_key, category="visitor_gift", amount=reward.get("coins", 0), details=reward))
    return reward
