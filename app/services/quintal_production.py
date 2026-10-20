"""Server-clock production. Callers hold the wallet lock until commit/rollback."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import random
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.quintal_models import (QuintalFacility, QuintalResource, QuintalTask, QuintalPlot,
    QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference)
from app.persistence_models import HabitatItem, HabitatTransaction, RemoteCat
from app.services.habitat_service import wallet_for, walkable_cells, item_cells, validate_placements
from app.services.habitat_interactions import reachable
from app.services.habitat_pricing import effective_build_seconds, effective_price
from app.services.quintal_catalog import CROPS, RECIPES, FACILITIES, ORDERS, SATISFACTION, BOND_REWARDS, catalog, order_product_options
from app.services.quintal_rewards import record_bond_reward, acknowledge_rewards
from app.services.quintal_return import record_reaction, visible_updates, acknowledge

CALENDAR = ZoneInfo("America/Sao_Paulo")
PROGRESS_METRICS = {"cat_set", "water_cat", "plant", "water_crop", "harvest", "cook", "meal", "play", "fish", "egg", "milk_collection", "facility", "favorite", "bond"}

def utc(value):
    if value is None: return None
    if isinstance(value, str): value = datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

def day(now): return now.astimezone(CALENDAR).date().isoformat()
def iso(value): return utc(value).isoformat() if value else None

async def rows(session, model, user_id):
    query = select(model).where(model.user_id == user_id)
    if model is QuintalTask: query = query.where(model.status.in_(("active", "ready")))
    return list((await session.scalars(query)).all())

async def counter(session, user_id, metric, amount=0, *, occurred_at=None):
    row = await session.get(QuintalCounter, (user_id, metric))
    if row is None:
        row = QuintalCounter(user_id=user_id, metric=metric, quantity=0, projected_quantity=0)
        session.add(row)
    row.quantity += amount
    if amount > 0 and metric in PROGRESS_METRICS:
        session.add(HabitatTransaction(user_id=user_id,
            event_key=f"qactivity:{metric}:{row.quantity}", category="quintal_activity", amount=0,
            details={"metric": metric, "quantity": amount, "value": row.quantity,
                     "occurred_at": iso(occurred_at or datetime.now(UTC)), "projected": False}))
    await session.flush()
    return row.quantity

async def stock(session, user_id, changes):
    owned = {r.resource: r for r in await rows(session, QuintalResource, user_id)}
    if any((owned[k].quantity if k in owned else 0) + n < 0 for k, n in changes.items()):
        raise ValueError("Ingredientes ou porções insuficientes.")
    for key, amount in changes.items():
        if key not in owned:
            owned[key] = QuintalResource(user_id=user_id, resource=key, quantity=0)
            session.add(owned[key])
        owned[key].quantity += amount
    await session.flush()

async def levels(session, user_id):
    return {f.kind: f.level for f in await rows(session, QuintalFacility, user_id)}

async def cat_care(session, wallet, cat_id):
    row = await session.get(QuintalCatCare, (wallet.user_id, cat_id))
    if row is None:
        old = (wallet.care_state or {}).get("cats", {}).get(str(cat_id), {})
        row = QuintalCatCare(user_id=wallet.user_id, cat_id=cat_id, state={"affinity": old.get("affinity", 0), "bonuses": {}})
        session.add(row)
        await session.flush()
    return row

async def preference(session, user_id, cat_id, recipe, rng):
    row = await session.get(QuintalPreference, (user_id, cat_id, recipe))
    if row is None:
        draw = rng.random()
        row = QuintalPreference(user_id=user_id, cat_id=cat_id, recipe=recipe,
            state={"level": 1 if draw < .15 else 2 if draw < .4 else 3, "known": False, "streak": 0})
        session.add(row)
        await session.flush()
    return row

def evolve(state, now, rng):
    level = state["level"]
    last = utc(state.get("evaluated_at"))
    if level in (0, 6) or (last and now - last < timedelta(hours=24)): return
    state["evaluated_at"] = now.isoformat()
    draw = rng.random()
    if level == 1:
        state["streak"] = state.get("streak", 0) + 1
        risk = (.03, .06, .10)[min(2, state["streak"] - 1)]
        if draw < risk: state["level"] = 0
        elif draw < risk + .07: state["level"] = 2
    elif level == 2:
        if draw < .12: state["level"] = 3
        elif draw < .20: state["level"] = 1
    elif level == 3:
        if draw < .10: state["level"] = 4
        elif draw < .18: state["level"] = 2
    elif level == 4 and draw < .05: state["level"] = 5
    elif level == 5 and draw < .02: state["level"] = 6

def bond(state, action, points, now):
    bonuses = deepcopy(state.get("bonuses", {}))
    if bonuses.get(action) == day(now): return
    bonuses[action] = day(now)
    state["bonuses"] = bonuses
    state["affinity"] = state.get("affinity", 0) + points


def play_care_bonus(state, now):
    """Current play contribution, before the yard's aggregate satisfaction cap."""
    at = utc(state.get('played_at'))
    if not at or state.get('assisted'):
        return 0.0
    return 20 * max(0, 1 - max(0, (now - at).total_seconds()) / 86400)

def resident_animals(state, now):
    animals = deepcopy(state.get("animals", []))
    while len(animals) < state.get("count", 0):
        slot = len(animals)
        animal = {"id": str(uuid4()), "slot": slot, "cursor": state.get("cursor") or iso(now), "progress": 0, "ready": 0}
        # Only legacy aggregate rows inherit their existing supplies; newly
        # constructed residents are added explicitly with empty food storage.
        if "animals" not in state:
            for key in ("food_start", "food_until"):
                if state.get(key): animal[key] = state[key]
        animals.append(animal)
    return animals

async def meals(session, wallet, care, now, rng):
    state = deepcopy(care.state)
    portions = state.get("portions", 0)
    recipe = state.get("recipe", "basic")
    last = utc(state.get("last_meal_at"))
    served = utc(state.get("served_at")) or now
    next_meal = max(last + timedelta(hours=12), served) if last else served
    while portions > 0 and next_meal <= now:
        portions -= 1
        pref = None if recipe == "basic" else await preference(session, wallet.user_id, care.cat_id, recipe, rng)
        p = deepcopy(pref.state) if pref else {"level": 3}
        previous_level = p["level"]
        p["known"] = True
        if previous_level == 0:
            await counter(session, wallet.user_id, "refusal", 1)
            state["last_reaction"] = {"kind": "refused", "recipe": recipe, "at": iso(next_meal), "lost": 1,
                "portions_before": portions + 1, "portions_after": portions}
            state['last_reaction']['event_id'] = record_reaction(session, wallet.user_id, care.cat_id, state['last_reaction'])
            if pref: pref.state = p
            continue
        state["last_meal_at"] = iso(next_meal)
        state["meal_satisfaction"] = (8 if recipe == "basic" else RECIPES[recipe][4]) * SATISFACTION[previous_level]
        bond(state, "meal", 2, next_meal)
        if previous_level == 6: bond(state, "favorite", 1, next_meal)
        if state.get("last_recipe") != recipe:
            for other in await rows(session, QuintalPreference, wallet.user_id):
                if other.cat_id == care.cat_id and other.recipe != recipe:
                    other.state = {**other.state, "streak": 0}
        state["last_recipe"] = recipe
        if pref:
            evolve(p, next_meal, rng)
            pref.state = p
            if p["level"] == 6 and previous_level != 6: await counter(session, wallet.user_id, "favorite", 1, occurred_at=next_meal)
        state["last_reaction"] = {"kind": "meal", "recipe": recipe, "at": iso(next_meal), "preference": previous_level, "next_preference": p["level"],
            "portions_before": portions + 1, "portions_after": portions}
        state['last_reaction']['event_id'] = record_reaction(session, wallet.user_id, care.cat_id, state['last_reaction'])
        await counter(session, wallet.user_id, "meal", 1, occurred_at=next_meal)
        await counter(session, wallet.user_id, "care_day:" + day(next_meal), 1)
        next_meal += timedelta(hours=12)
    state["portions"] = portions
    care.state = state

async def reconcile(session, wallet, now=None, rng=None):
    now, rng = now or datetime.now(UTC), rng or random.SystemRandom()
    for task in await rows(session, QuintalTask, wallet.user_id):
        if task.status == "active" and utc(task.ready_at) <= now:
            task.status = "ready"
            if task.kind == "cook": await counter(session, wallet.user_id, "cook", 1, occurred_at=utc(task.ready_at))
    for resident in await rows(session, QuintalResident, wallet.user_id):
        s = deepcopy(resident.state)
        animals = resident_animals(s, now)
        previous_amount = sum(a.get("ready", 0) for a in animals)
        cycle, capacity = (21600, 2) if resident.kind == "coop" else (28800, 3) if resident.kind == "orchard" else (28800, 2)
        for a in animals:
            cursor = utc(a.get("cursor")) or now
            start = max(cursor, utc(a.get("food_start")) or now, utc(s.get("water_start")) or now)
            end = min(now, utc(a.get("food_until")) or cursor, utc(s.get("water_until")) or cursor)
            if resident.kind == "orchard": start, end = cursor, now
            ready, progress = a.get("ready", 0), a.get("progress", 0)
            if ready < capacity and end > start:
                progress += (end - start).total_seconds()
                per_cycle = 3 if resident.kind == "orchard" else 1
                ready += min(capacity - ready, int(progress // cycle) * per_cycle)
                progress = 0 if ready >= capacity else progress % cycle
            a.update(ready=ready, progress=progress, cursor=iso(now))
        amount = sum(a["ready"] for a in animals)
        s.update(ready=amount, animals=animals, cursor=iso(now))
        if amount > previous_amount: s["ready_since"] = iso(now)
        fed = [a for a in animals if utc(a.get("food_until")) and utc(a["food_until"]) > now]
        s["fed_count"] = len(fed)
        s["food_until"] = min((a["food_until"] for a in fed), default=None) if len(fed) == len(animals) else None
        resident.state = s
    for care in await rows(session, QuintalCatCare, wallet.user_id):
        if str(care.cat_id) in (wallet.selected_cat_ids or []): await meals(session, wallet, care, now, rng)
    care_state = deepcopy(wallet.care_state or {})
    legacy_cats = care_state.setdefault("cats", {})
    for care in await rows(session, QuintalCatCare, wallet.user_id):
        old_affinity = legacy_cats.get(str(care.cat_id), {}).get("affinity", 0)
        affinity = care.state.get("affinity", 0)
        for threshold, sku in BOND_REWARDS:
            if affinity < threshold: continue
            key = f"bond:{care.cat_id}:{threshold}"
            exists = await session.scalar(select(HabitatTransaction.id).where(HabitatTransaction.user_id == wallet.user_id, HabitatTransaction.event_key == key))
            if exists: continue
            preserved = threshold == 20 and old_affinity >= 20 and care_state.get("gift_claimed")
            if not preserved:
                session.add(HabitatItem(user_id=wallet.user_id, sku=sku, purchase_key=key))
                await record_bond_reward(session, wallet.user_id, care.cat_id, threshold, sku)
            session.add(HabitatTransaction(user_id=wallet.user_id, event_key=key, category="bond", amount=0, details={"cat_id": str(care.cat_id), "threshold": threshold, "sku": sku, "preserved": bool(preserved)}))
        legacy_cats[str(care.cat_id)] = {**legacy_cats.get(str(care.cat_id), {}), "affinity": affinity}
    care_state["production_cats"] = {str(c.cat_id): {k: c.state.get(k) for k in ("last_meal_at", "meal_satisfaction", "played_at", "water_until", "affinity", "assisted")} for c in await rows(session, QuintalCatCare, wallet.user_id)}
    wallet.care_state = care_state
    affinity = max((c.state.get("affinity", 0) for c in await rows(session, QuintalCatCare, wallet.user_id)), default=0)
    current = await counter(session, wallet.user_id, "bond")
    if affinity > current: await counter(session, wallet.user_id, "bond", affinity - current, occurred_at=now)
    await session.flush()

async def available_requirements(session, wallet):
    values = {r.metric: r.quantity for r in await rows(session, QuintalCounter, wallet.user_id)}
    values.update({"facility:" + k: v for k, v in (await levels(session, wallet.user_id)).items()})
    legacy = (wallet.care_state or {}).get("cats", {})
    values["care"] = max(values.get("care", 0), int(any(c.get("pet_day") or c.get("feed_day") for c in legacy.values())))
    return values

def resident_projection(resident, now):
    """Describe the next actionable state from reconciled, bounded production."""
    state = deepcopy(resident.state)
    orchard = resident.kind == 'orchard'
    cycle = 21600 if resident.kind == 'coop' else 28800
    capacity = 3 if orchard else 2
    next_times = []
    for animal in state.get('animals', []):
        animal['next_ready_at'] = None
        animal['capacity'] = capacity
        food_until = utc(animal.get('food_until')) or now
        water_until = utc(state.get('water_until')) or now
        full = animal.get('ready', 0) >= capacity
        pause = ('full' if full else
                 'needs_food' if not orchard and food_until <= now else
                 'needs_water' if not orchard and water_until <= now else None)
        animal['production_progress'] = {
            'observed_at': iso(now), 'cycle_seconds': cycle,
            'elapsed_seconds': min(cycle, max(0, animal.get('progress', 0))),
            'pause_reason': pause,
            'active_until': None if orchard else iso(min(food_until, water_until)),
            'expires_reason': 'needs_food' if food_until <= water_until else 'needs_water',
        }
        if animal.get('ready', 0) >= capacity:
            continue
        if not orchard:
            available_until = min(utc(animal.get('food_until')) or now,
                                  utc(state.get('water_until')) or now)
            if available_until <= now:
                continue
        remaining = max(0, cycle - animal.get('progress', 0))
        expected = now + timedelta(seconds=remaining)
        if orchard or expected <= available_until:
            next_times.append(expected)
            animal['next_ready_at'] = iso(expected)
    count = state.get('count', 0)
    status = ('full' if state.get('ready', 0) >= count * capacity else
              'needs_food' if not orchard and state.get('fed_count', 0) < count else
              'needs_water' if not orchard and (utc(state.get('water_until')) or now) <= now else
              'producing')
    progress_candidates = [a['production_progress'] for a in state.get('animals', [])
                           if a.get('ready', 0) < capacity]
    progress_candidates.sort(key=lambda item: (item['pause_reason'] is not None,
                                               -item['elapsed_seconds']))
    return {'kind': resident.kind, **state, 'capacity': count * capacity,
            'production_progress': progress_candidates[0] if progress_candidates else None,
            'production_status': status, 'next_ready_at': iso(min(next_times)) if next_times else None}

async def ensure_pair(session, wallet, cat_id):
    care = await cat_care(session, wallet, cat_id)
    state = deepcopy(care.state)
    used = {c.state.get("color_index") for c in await rows(session, QuintalCatCare, wallet.user_id) if c.cat_id != cat_id and str(c.cat_id) in (wallet.selected_cat_ids or [])}
    if state.get("color_index") is None or state["color_index"] in used:
        state["color_index"] = next((index for index in range(6) if index not in used), 0)
    items = await rows(session, HabitatItem, wallet.user_id)
    other_refs = {c.state.get(role) for c in await rows(session, QuintalCatCare, wallet.user_id)
                  if c.cat_id != cat_id for role in ("food_bowl", "water_bowl")}
    for role, sku in (("food_bowl", "food_bowl"), ("water_bowl", "water_bowl")):
        item = next((i for i in items if str(i.id) == state.get(role) and i.sku == sku
                     and str(i.id) not in other_refs), None)
        if item is None:
            item = next((i for i in items if i.sku == sku and str(i.id) not in other_refs and
                         ((i.care_state or {}).get("cat_id") == str(cat_id) or
                          i.purchase_key == f"pair:{cat_id}:{role[:4]}")), None)
        if item is None:
            item = next((i for i in items if i.sku == sku and str(i.id) not in other_refs
                         and not (i.care_state or {}).get("cat_id")), None)
        if item is not None:
            state[role] = str(item.id)
            if item.x is None or item.y is None:
                state["assisted"] = str(cat_id) in (wallet.selected_cat_ids or [])
        if item is None:
            item = HabitatItem(id=uuid4(), user_id=wallet.user_id, sku=sku, purchase_key=f"pair:{cat_id}:{role[:4]}", care_state={"cat_id": str(cat_id), "role": role})
            session.add(item)
            state[role] = str(item.id)
            state["assisted"] = str(cat_id) in (wallet.selected_cat_ids or [])
        item.care_state = {**(item.care_state or {}), "cat_id": str(cat_id), "role": role, "color_index": state["color_index"]}
    care.state = state
    await session.flush()
    return care

async def validate_bowls(session, wallet, items):
    free = walkable_cells(items, wallet.expansion_level)
    for care in await rows(session, QuintalCatCare, wallet.user_id):
        if str(care.cat_id) not in (wallet.selected_cat_ids or []):
            s = deepcopy(care.state)
            if s.get("portions", 0) and s.get("recipe") != "basic":
                await stock(session, wallet.user_id, {"ration:" + s["recipe"]: s["portions"]})
            s["portions"] = 0
            care.state = s
            for item in items:
                if str(item.id) in (s.get("food_bowl"), s.get("water_bowl")): item.x, item.y = None, None
            continue
        anchor = (wallet.cat_anchors or {}).get(str(care.cat_id))
        if care.state.get("assisted"): continue
        for role in ("food_bowl", "water_bowl"):
            item = next((i for i in items if str(i.id) == care.state.get(role)), None)
            if not item or item.x is None or not anchor: raise ValueError("Cada gato precisa dos dois potes posicionados. Reponha o pote ou retire o gato junto.")
            goals = {(x + dx, y + dy) for x, y in item_cells(item.sku, item.x, item.y, item.rotation) for dx, dy in ((1,0),(-1,0),(0,1),(0,-1))} & free
            if not reachable(tuple(anchor), goals, free, items): raise ValueError("Deixe um caminho livre entre o gato e seus potes.")

async def projection(session, user_id, now=None):
    now = now or datetime.now(UTC)
    wallet = await wallet_for(session, user_id)
    for raw in wallet.selected_cat_ids or []:
        try: cat_id = UUID(raw)
        except ValueError: continue
        await ensure_pair(session, wallet, cat_id)
    await reconcile(session, wallet, now)
    requirements = await available_requirements(session, wallet)
    facilities = await rows(session, QuintalFacility, user_id)
    existing = {f.kind: f for f in facilities}
    offers = []
    for key, definition in FACILITIES.items():
        kind, level = key.split(":")
        f = existing.get(kind)
        if int(level) != (f.level if f else 0) + 1: continue
        reasons = []
        if wallet.expansion_level < definition[3]: reasons.append(f"Quintal expansão {definition[3]}")
        labels = {'care': 'Cuidados com os gatos', 'harvest': 'Colheitas',
                  'cook': 'Lotes preparados', 'fish': 'Capturas', 'egg': 'Ovos coletados',
                  'milk_collection': 'Coletas de leite', 'crop:corn': 'Colheitas de milho',
                  'facility:kitchen': 'Nível da cozinha', 'facility:coop': 'Nível do galinheiro',
                  'facility:garden': 'Nível da horta', 'facility:pier': 'Nível do píer'}
        for metric, needed in definition[4].items():
            if requirements.get(metric, 0) < needed:
                reasons.append(f"{labels.get(metric, metric)}: {requirements.get(metric, 0)}/{needed}")
        offers.append({"id": key, "available": not reasons and not (f and f.target_level), "reasons": reasons})
    seen = await counter(session, user_id, "summary_seen")
    updates = []
    for task in await rows(session, QuintalTask, user_id):
        if task.status == "ready" and utc(task.ready_at).timestamp() > seen: updates.append({"_source": f'task:{task.id}', "kind": task.kind, "target": task.target, "id": str(task.id), "outputs": task.outputs})
    for plot in await rows(session, QuintalPlot, user_id):
        if plot.crop and utc(plot.ready_at) <= now and utc(plot.ready_at).timestamp() > seen: updates.append({"_source": f'plot:{plot.slot}:{iso(plot.started_at)}', "kind": "harvest", "target": plot.crop, "slot": plot.slot, "quantity": CROPS[plot.crop][3]})
    for resident in await rows(session, QuintalResident, user_id):
        if resident.state.get("ready", 0) and utc(resident.state.get("ready_since")) and utc(resident.state["ready_since"]).timestamp() > seen: updates.append({"_source": f'resident:{resident.kind}:{resident.state["ready_since"]}', "kind": "resident", "target": resident.kind, "quantity": resident.state["ready"]})
    for care in await rows(session, QuintalCatCare, user_id):
        reaction = care.state.get("last_reaction", {})
        if not reaction.get('event_id') and utc(reaction.get("at")) and utc(reaction["at"]).timestamp() > seen: updates.append({"_source": f'reaction:{care.cat_id}:{reaction["at"]}', "kind": reaction["kind"], "target": str(care.cat_id), "recipe": reaction["recipe"]})
    for facility in facilities:
        if facility.target_level and utc(facility.ready_at) <= now:
            updates.append({'_source': f'build:{facility.kind}:{facility.target_level}:{iso(facility.ready_at)}', 'kind': 'build', 'target': facility.kind, 'level': facility.target_level})
    updates = await visible_updates(session, user_id, updates)
    return {
        "server_now": iso(now), "calendar_day": day(now), "revision": wallet.layout_revision,
        "catalog": catalog(), "offers": offers, "counters": requirements,
        "updates": updates,
        "stock": {r.resource: r.quantity for r in await rows(session, QuintalResource, user_id)},
        "facilities": [{"kind": f.kind, "level": f.level, "target_level": f.target_level, "started_at": iso(f.started_at), "ready_at": iso(f.ready_at)} for f in facilities],
        "plots": [{"slot": p.slot, "crop": p.crop, "started_at": iso(p.started_at), "ready_at": iso(p.ready_at), "watered": bool(p.watered)} for p in sorted(await rows(session, QuintalPlot, user_id), key=lambda p: p.slot)],
        "tasks": [{"id": str(t.id), "kind": t.kind, "target": t.target, "status": t.status, "started_at": iso(t.started_at), "ready_at": iso(t.ready_at), "inputs": t.inputs, "outputs": t.outputs} for t in await rows(session, QuintalTask, user_id) if t.status in ("active", "ready")],
        "residents": [resident_projection(r, now) for r in await rows(session, QuintalResident, user_id)],
        "cats": [{"cat_id": str(c.cat_id), **c.state,
            "next_appetite_at": iso(utc(c.state["last_meal_at"]) + timedelta(hours=12)) if c.state.get("last_meal_at") else None,
            "hunger": min(1., max(0., (now - utc(c.state["last_meal_at"])).total_seconds() / 43200)) if c.state.get("last_meal_at") else 1.,
            "water_available": bool(c.state.get("water_until") and utc(c.state["water_until"]) > now)} for c in await rows(session, QuintalCatCare, user_id)],
        "preferences": [{"cat_id": str(p.cat_id), "recipe": p.recipe, **p.state} for p in await rows(session, QuintalPreference, user_id) if p.state.get("known")],
    }

async def owned_cat(session, wallet, target):
    try: cat_id = UUID(target)
    except (ValueError, TypeError) as error: raise ValueError("Gato inválido.") from error
    cat = await session.scalar(select(RemoteCat).where(RemoteCat.user_id == wallet.user_id, RemoteCat.client_id == cat_id, RemoteCat.deleted_at.is_(None)))
    if not cat or not (cat.payload or {}).get("is_owned") or target not in (wallet.selected_cat_ids or []):
        raise ValueError("Escolha um gato seu que esteja no Quintal.")
    return cat_id

async def operation(session, user_id, action, target, data, key, now=None, rng=None):
    now, rng = now or datetime.now(UTC), rng or random.SystemRandom()
    wallet = await wallet_for(session, user_id)
    event_key = f"quintal:{key}"
    fingerprint = {"action": action, "target": target, "data": data}
    old = await session.scalar(select(HabitatTransaction).where(HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == event_key))
    if old:
        if old.details.get("request") != fingerprint: raise ValueError("Chave já utilizada para outra operação.")
        return old.details.get("result", {})
    await reconcile(session, wallet, now, rng)
    result, price = {}, 0
    lv = await levels(session, user_id)
    if action == "ack_rewards":
        result["acknowledged"] = await acknowledge_rewards(session, user_id, data.get("event_ids", []))
    elif action == "ack_return":
        result['acknowledged'] = await acknowledge(session, user_id, data.get('event_ids', []))
    elif action == "rename_resident":
        resident = await session.get(QuintalResident, (user_id, target))
        slot, name = data.get("slot"), data.get("name")
        if not resident or type(slot) is not int or not 0 <= slot < resident.state.get("count", 0) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 32:
            raise ValueError("Informe um nome de até 32 caracteres para um residente seu.")
        resident.state = {**resident.state, "names": {**resident.state.get("names", {}), str(slot): name.strip()}}
    elif action == "build":
        definition = FACILITIES.get(target)
        if not definition: raise ValueError("Instalação inexistente.")
        kind, level_text = target.split(":")
        level = int(level_text)
        f = await session.get(QuintalFacility, (user_id, kind))
        values = await available_requirements(session, wallet)
        if level != lv.get(kind, 0) + 1 or wallet.expansion_level < definition[3] or any(values.get(k, 0) < v for k, v in definition[4].items()):
            raise ValueError("Requisitos da instalação ainda não atendidos.")
        if f and f.target_level: raise ValueError("Esta instalação já tem uma obra em andamento.")
        price = effective_price(definition[1])
        if wallet.balance < price: raise ValueError("Moedas insuficientes.")
        if not f:
            f = QuintalFacility(user_id=user_id, kind=kind, level=0)
            session.add(f)
        f.target_level, f.ready_at = level, now + timedelta(seconds=effective_build_seconds(definition[2]))
        f.started_at = now
    elif action == "finish_build":
        f = await session.get(QuintalFacility, (user_id, target))
        if not f or not f.target_level or utc(f.ready_at) > now: raise ValueError("A obra ainda não está pronta.")
        f.level, f.target_level, f.ready_at = f.target_level, 0, None
        f.started_at = None
        if target == "pier" and not await session.get(QuintalFacility, (user_id, "rod")):
            session.add(QuintalFacility(user_id=user_id, kind="rod", level=1, target_level=0))
        if target == "garden":
            for slot in range(f.level * 4):
                if not await session.get(QuintalPlot, (user_id, slot)): session.add(QuintalPlot(user_id=user_id, slot=slot, watered=0))
        if target in ("coop", "pasture", "orchard"):
            resident = await session.get(QuintalResident, (user_id, target))
            if not resident:
                resident = QuintalResident(user_id=user_id, kind=target, state={})
                session.add(resident)
            resident.state = {**resident.state, "count": (2 * f.level if target == "coop" else f.level if target == "pasture" else 2), "cursor": iso(now)}
            state = {**resident.state, "animals": resident.state.get("animals", [])}
            resident.state = {**state, "animals": resident_animals(state, now)}
        await counter(session, user_id, "facility", 1, occurred_at=now)
    elif action in ("plant", "water_crop", "harvest"):
        try: slot = int(target)
        except ValueError as error: raise ValueError("Canteiro inválido.") from error
        plot = await session.get(QuintalPlot, (user_id, slot))
        if not plot: raise ValueError("Canteiro não liberado.")
        if action == "plant":
            crop = data.get("crop")
            if crop not in CROPS or CROPS[crop][1] > lv.get("garden", 0): raise ValueError("Cultivo não liberado.")
            if plot.crop: raise ValueError("Colha antes de plantar novamente.")
            plot.crop, plot.started_at, plot.ready_at, plot.watered = crop, now, now + timedelta(seconds=CROPS[crop][2]), 0
            await counter(session, user_id, "plant", 1, occurred_at=now)
        elif action == "water_crop":
            if not plot.crop or plot.watered or utc(plot.ready_at) <= now: raise ValueError("Este cultivo não precisa de rega.")
            plot.ready_at = now + (utc(plot.ready_at) - now) * .85
            plot.watered = 1
            await counter(session, user_id, "water_crop", 1, occurred_at=now)
        else:
            if not plot.crop or utc(plot.ready_at) > now: raise ValueError("Cultivo ainda não pronto.")
            crop = plot.crop
            await stock(session, user_id, {crop: CROPS[crop][3]})
            await counter(session, user_id, "harvest", 1, occurred_at=now)
            await counter(session, user_id, "crop:" + crop, 1)
            plot.crop, plot.started_at, plot.ready_at, plot.watered = None, None, None, 0
    elif action in ("cook", "order"):
        tasks = await rows(session, QuintalTask, user_id)
        active = sorted([t for t in tasks if t.kind == action and t.status == "active"], key=lambda t: utc(t.ready_at))
        if action == "cook":
            definition = RECIPES.get(target)
            if not definition or lv.get("kitchen", 0) < 1 or lv.get(definition[5], 0) < definition[6]: raise ValueError("Receita ainda não liberada.")
            if target in ("beef", "special") and lv.get("market", 0) < 1: raise ValueError("Abra o mercado para liberar esta receita.")
            if len(active) >= (2 if lv.get("kitchen", 0) == 1 else 4): raise ValueError("Fila da cozinha cheia.")
            inputs, seconds, outputs = definition[1], definition[2], {"ration:" + target: definition[3]}
        else:
            definition = ORDERS.get(target)
            if not definition or lv.get("market", 0) < 1 or wallet.expansion_level < definition[5]: raise ValueError("Encomenda não liberada.")
            if any(t.kind == "order" and t.status in ("active", "ready") for t in tasks): raise ValueError("Colete a encomenda anterior primeiro.")
            if data.get("payment") not in ("coins", "products"): raise ValueError("Escolha moedas ou produtos.")
            quantity = data.get("quantity", definition[4])
            if type(quantity) is not int or not definition[4] <= quantity <= 100 or quantity % definition[4]:
                raise ValueError(f"Escolha de {definition[4]} a 100 unidades, em lotes de {definition[4]}.")
            batches = quantity // definition[4]
            inputs = {}
            if data["payment"] == "products":
                option = next((o for o in order_product_options(target)
                               if o["id"] == data.get("product_option", "harvest")), None)
                if option is None: raise ValueError("Troca de produtos inválida.")
                if option.get("facility") and lv.get(option["facility"], 0) < option["level"]:
                    raise ValueError("Recupere o pomar para trocar suas frutas.")
                inputs = {k: v * batches for k, v in option["products"].items()}
            elif data.get("product_option") is not None:
                raise ValueError("Escolha moedas ou uma troca de produtos.")
            price = effective_price(definition[1]) * batches if data["payment"] == "coins" else 0
            if wallet.balance < price: raise ValueError("Moedas insuficientes.")
            seconds, outputs = definition[3] * batches, {target: quantity}
        await stock(session, user_id, {k: -v for k, v in inputs.items()})
        start = max(now, utc(active[-1].ready_at)) if active else now
        task = QuintalTask(id=uuid4(), user_id=user_id, kind=action, target=target, status="active", started_at=start, ready_at=start + timedelta(seconds=seconds), inputs=inputs, outputs=outputs)
        session.add(task)
        result = {"task_id": str(task.id)}
    elif action in ("collect", "cancel"):
        try: task = await session.get(QuintalTask, UUID(target))
        except ValueError as error: raise ValueError("Tarefa inválida.") from error
        if not task or task.user_id != user_id: raise ValueError("Tarefa inexistente.")
        if action == "collect":
            if task.status != "ready": raise ValueError("Tarefa ainda não pronta ou já coletada.")
            await stock(session, user_id, task.outputs)
            task.status = "collected"
            await counter(session, user_id, "collected:" + task.kind, 1)
        else:
            if task.kind != "cook" or task.status != "active" or utc(task.started_at) <= now: raise ValueError("Só preparos em espera podem ser cancelados.")
            await stock(session, user_id, task.inputs)
            task.status = "cancelled"
            duration = utc(task.ready_at) - utc(task.started_at)
            for following in await rows(session, QuintalTask, user_id):
                if following.kind == "cook" and following.status == "active" and utc(following.started_at) >= utc(task.ready_at):
                    following.started_at = utc(following.started_at) - duration
                    following.ready_at = utc(following.ready_at) - duration
    elif action in ("feed_residents", "water_residents", "collect_residents"):
        resident = await session.get(QuintalResident, (user_id, target))
        if not resident: raise ValueError("Espaço não liberado.")
        s = deepcopy(resident.state)
        count = s["count"]
        animals = resident_animals(s, now)
        if action == "collect_residents":
            selected = animals
            resident_id = data.get("resident_id")
            if resident_id is not None:
                if target not in ("pasture", "orchard") or not isinstance(resident_id, str):
                    raise ValueError("Este espaço não oferece coleta individual.")
                selected = [a for a in animals if a["id"] == resident_id]
                if not selected: raise ValueError("Residente não encontrado neste espaço.")
            quantity = sum(a.get("ready", 0) for a in selected)
            if not quantity: raise ValueError("Ainda não há produção pronta.")
            if target == "orchard": outputs = {"apple": sum(a["ready"] for a in selected if a["slot"] % 2 == 0), "pear": sum(a["ready"] for a in selected if a["slot"] % 2 == 1)}
            else: outputs = {"egg" if target == "coop" else "milk": quantity}
            await stock(session, user_id, outputs)
            for a in selected: a.update(ready=0, cursor=iso(now))
            s["ready"] = sum(a.get("ready", 0) for a in animals)
            s["cursor"] = iso(now)
            result = {"collected": {k: v for k, v in outputs.items() if v}, "resident_id": resident_id}
            await counter(session, user_id, "egg" if target == "coop" else "milk" if target == "pasture" else "fruit", quantity, occurred_at=now)
            if target == "pasture": await counter(session, user_id, "milk_collection", 1, occurred_at=now)
        elif target == "orchard": raise ValueError("Fruteiras não usam comedouro ou bebedouro.")
        elif action == "feed_residents":
            hungry = [a for a in animals if not utc(a.get("food_until")) or utc(a["food_until"]) <= now]
            if not hungry: raise ValueError("O comedouro ainda está abastecido.")
            count = len(hungry)
            await stock(session, user_id, {"corn": -count} if target == "coop" else {"wheat": -2 * count, "corn": -count})
            for a in hungry: a.update(food_start=iso(now), food_until=iso(now + timedelta(hours=24)))
            s["fed_count"] = len(animals)
        else:
            if s.get("water_day") == day(now): raise ValueError("Água já abastecida hoje.")
            s.update(water_day=day(now), water_start=iso(now), water_until=iso(now + timedelta(hours=24)))
        resident.state = {**s, "animals": animals}
    elif action in ("add_cat", "remove_cat"):
        if action == "add_cat":
            try: cat_id = UUID(target)
            except ValueError as error: raise ValueError("Gato inválido.") from error
            cat = await session.scalar(select(RemoteCat).where(RemoteCat.user_id == user_id, RemoteCat.client_id == cat_id, RemoteCat.deleted_at.is_(None)))
            if not cat or not (cat.payload or {}).get("is_owned"): raise ValueError("Selecione um gato de Meus Gatos.")
            if target not in (wallet.selected_cat_ids or []) and len(wallet.selected_cat_ids or []) >= 3 + wallet.expansion_level: raise ValueError("Quintal na capacidade máxima.")
            care = await ensure_pair(session, wallet, cat_id)
            care.state = {**care.state, "assisted": False}
            positions = {role: data.get(role) for role in ("anchor", "food", "water")}
            if any(not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v) for v in positions.values()): raise ValueError("Posicione o gato e os dois potes.")
            items = await rows(session, HabitatItem, user_id)
            for role, name in (("food_bowl", "food"), ("water_bowl", "water")):
                item = next(i for i in items if str(i.id) == care.state[role])
                item.x, item.y, item.rotation = *positions[name], 0
            placements = [{"item_id": str(i.id), "x": i.x, "y": i.y, "rotation": i.rotation} for i in items if i.x is not None]
            validate_placements(items, placements, wallet.expansion_level)
            free = walkable_cells(items, wallet.expansion_level)
            anchor = tuple(positions["anchor"])
            if anchor not in free or any(tuple(v) == anchor for k,v in (wallet.cat_anchors or {}).items() if k != target): raise ValueError("Escolha uma posição livre para o gato.")
            wallet.selected_cat_ids = list(dict.fromkeys([*(wallet.selected_cat_ids or []), target]))
            wallet.cat_anchors = {**(wallet.cat_anchors or {}), target: list(anchor)}
        else:
            await owned_cat(session, wallet, target)
            wallet.selected_cat_ids = [c for c in wallet.selected_cat_ids if c != target]
            wallet.cat_anchors = {k:v for k,v in (wallet.cat_anchors or {}).items() if k != target}
            items = await rows(session, HabitatItem, user_id)
        await validate_bowls(session, wallet, items)
        if action == "add_cat": await counter(session, user_id, "cat_set", 1, occurred_at=now)
    elif action in ("assign_bowls", "serve", "water_cat"):
        cat_id = await owned_cat(session, wallet, target)
        care = await cat_care(session, wallet, cat_id)
        s = deepcopy(care.state)
        if action == "assign_bowls":
            items = await rows(session, HabitatItem, user_id)
            pair = []
            for role, skus in (("food_bowl", {"food_bowl"}), ("water_bowl", {"water_bowl"})):
                item = next((i for i in items if str(i.id) == data.get(role) and i.sku in skus and i.x is not None), None)
                if not item: raise ValueError("Posicione os dois potes antes de associar.")
                for other in await rows(session, QuintalCatCare, user_id):
                    if other.cat_id != cat_id and other.state.get(role) == str(item.id): raise ValueError("Este pote já pertence a outro gato.")
                free = walkable_cells(items, wallet.expansion_level)
                anchor = (wallet.cat_anchors or {}).get(target)
                goals = {(x + dx, y + dy) for x, y in item_cells(item.sku, item.x, item.y, item.rotation) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))} & free
                if not anchor or not reachable(tuple(anchor), goals, free, items): raise ValueError("O gato precisa alcançar os dois potes.")
                pair.append(str(item.id))
            s.update(food_bowl=pair[0], water_bowl=pair[1])
        else:
            if s.get("assisted") or not s.get("food_bowl") or not s.get("water_bowl"): raise ValueError("Posicione os dois potes deste gato primeiro.")
            await validate_bowls(session, wallet, await rows(session, HabitatItem, user_id))
            if action == "water_cat":
                if s.get("water_day") == day(now): raise ValueError("Água deste gato já abastecida hoje.")
                s.update(water_day=day(now), water_until=iso(now + timedelta(hours=24)))
                await counter(session, user_id, "care", 1)
                await counter(session, user_id, "water_cat", 1, occurred_at=now)
                await counter(session, user_id, "care_day:" + day(now), 1)
            else:
                recipe, count = data.get("recipe"), data.get("quantity", 1)
                if recipe not in {"basic", *RECIPES} or type(count) is not int or not 1 <= count <= 2: raise ValueError("Escolha uma ração e até duas porções.")
                refused = False
                if recipe != "basic":
                    p = await preference(session, user_id, cat_id, recipe, rng)
                    if p.state.get("known") and p.state["level"] == 0 and not data.get("confirm_refusal"): raise ValueError("O gato odeia esta receita. Confirme a perda da porção.")
                    await stock(session, user_id, {"ration:" + recipe: -count})
                    refused = p.state["level"] == 0
                else:
                    if count != 1 or s.get("basic_day") == day(now): raise ValueError("Uma porção básica por gato a cada dia.")
                    s["basic_day"] = day(now)
                if s.get("portions", 0) and s.get("recipe") != "basic": await stock(session, user_id, {"ration:" + s["recipe"]: s["portions"]})
                s.update(recipe=recipe, portions=count, served_at=iso(now))
                if refused:
                    s.update(portions=0, last_reaction={"kind": "refused", "recipe": recipe, "at": iso(now), "lost": count,
                        "portions_before": count, "portions_after": 0})
                    s["last_reaction"]["event_id"] = record_reaction(session, user_id, cat_id, s["last_reaction"])
                    await counter(session, user_id, "refusal", count)
        care.state = s
        if action == "serve": await meals(session, wallet, care, now, rng)
    else: raise ValueError("Operação desconhecida.")
    wallet.balance -= price
    wallet.layout_revision += 1
    session.add(HabitatTransaction(user_id=user_id, event_key=event_key, category="quintal", amount=-price, details={"request": fingerprint, "result": result}))
    await session.flush()
    return result
