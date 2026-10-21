"""Server-clock production. Callers hold the wallet lock until commit/rollback."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import random
from uuid import UUID, uuid4
from sqlalchemy import select
from app.quintal_models import (QuintalFacility, QuintalTask, QuintalPlot,
    QuintalResident, QuintalCatCare)
from app.persistence_models import HabitatItem, HabitatTransaction, RemoteCat
from app.services.habitat_service import wallet_for, walkable_cells, item_cells, validate_placements
from app.services.habitat_interactions import reachable
from app.services.habitat_pricing import effective_build_seconds, effective_price
from app.services.quintal_catalog import CROPS, RECIPES, FACILITIES, ORDERS, order_product_options
from app.services.quintal_rewards import acknowledge_rewards
from app.services.quintal_return import record_reaction, acknowledge

from app.services.quintal_clock import utc as utc, day as day, iso as iso
from app.services.quintal_inventory import rows as rows, counter as counter, stock as stock, levels as levels, available_requirements as available_requirements
from app.services.quintal_care import cat_care as cat_care, preference as preference, evolve as evolve, bond as bond, play_care_bonus as play_care_bonus, meals as meals, ensure_pair as ensure_pair, validate_bowls as validate_bowls, owned_cat as owned_cat
from app.services.quintal_simulation import resident_animals as resident_animals, reconcile as reconcile
from app.services.quintal_projection import resident_projection as resident_projection, projection as projection
from app.services.quintal_clock import CALENDAR as CALENDAR
from app.services.quintal_inventory import PROGRESS_METRICS as PROGRESS_METRICS

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
