"""Server-clock production. Callers hold the wallet lock until commit/rollback."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID
from sqlalchemy import select
from app.quintal_models import QuintalCatCare
from app.persistence_models import HabitatItem, RemoteCat
from app.services.habitat_service import walkable_cells, item_cells, validate_placements
from app.services.habitat_interactions import reachable
from app.services.quintal_catalog import RECIPES
from app.services.quintal_return import record_reaction


from app.services.quintal_clock import day, iso
from app.services.quintal_inventory import rows, counter, stock
from app.services.quintal_care import (
    cat_care,
    preference,
    meals,
    ensure_pair,
    validate_bowls,
    owned_cat,
)
from .context import ProductionCommand, CommandOutcome


async def add_cat(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    action = command.action
    target = command.target
    data = command.data
    now = command.now
    wallet = command.wallet
    result = {}
    price = 0
    if action == "add_cat":
        try:
            cat_id = UUID(target)
        except ValueError as error:
            raise ValueError("Gato inválido.") from error
        cat = await session.scalar(
            select(RemoteCat).where(
                RemoteCat.user_id == user_id,
                RemoteCat.client_id == cat_id,
                RemoteCat.deleted_at.is_(None),
            )
        )
        if not cat or not (cat.payload or {}).get("is_owned"):
            raise ValueError("Selecione um gato de Meus Gatos.")
        if (
            target not in (wallet.selected_cat_ids or [])
            and len(wallet.selected_cat_ids or []) >= 3 + wallet.expansion_level
        ):
            raise ValueError("Quintal na capacidade máxima.")
        care = await ensure_pair(session, wallet, cat_id)
        care.state = {**care.state, "assisted": False}
        positions = {role: data.get(role) for role in ("anchor", "food", "water")}
        if any(
            (
                not isinstance(v, list)
                or len(v) != 2
                or any((type(n) is not int for n in v))
                for v in positions.values()
            )
        ):
            raise ValueError("Posicione o gato e os dois potes.")
        items = await rows(session, HabitatItem, user_id)
        for role, name in (("food_bowl", "food"), ("water_bowl", "water")):
            item = next((i for i in items if str(i.id) == care.state[role]))
            item.x, item.y, item.rotation = (*positions[name], 0)
        placements = [
            {"item_id": str(i.id), "x": i.x, "y": i.y, "rotation": i.rotation}
            for i in items
            if i.x is not None
        ]
        validate_placements(items, placements, wallet.expansion_level)
        free = walkable_cells(items, wallet.expansion_level)
        anchor = tuple(positions["anchor"])
        if anchor not in free or any(
            (
                tuple(v) == anchor
                for k, v in (wallet.cat_anchors or {}).items()
                if k != target
            )
        ):
            raise ValueError("Escolha uma posição livre para o gato.")
        wallet.selected_cat_ids = list(
            dict.fromkeys([*(wallet.selected_cat_ids or []), target])
        )
        wallet.cat_anchors = {**(wallet.cat_anchors or {}), target: list(anchor)}
    else:
        await owned_cat(session, wallet, target)
        wallet.selected_cat_ids = [c for c in wallet.selected_cat_ids if c != target]
        wallet.cat_anchors = {
            k: v for k, v in (wallet.cat_anchors or {}).items() if k != target
        }
        items = await rows(session, HabitatItem, user_id)
    await validate_bowls(session, wallet, items)
    if action == "add_cat":
        await counter(session, user_id, "cat_set", 1, occurred_at=now)
    return CommandOutcome(result, price)


async def assign_bowls(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    action = command.action
    target = command.target
    data = command.data
    now = command.now
    rng = command.rng
    wallet = command.wallet
    result = {}
    price = 0
    cat_id = await owned_cat(session, wallet, target)
    care = await cat_care(session, wallet, cat_id)
    s = deepcopy(care.state)
    if action == "assign_bowls":
        items = await rows(session, HabitatItem, user_id)
        pair = []
        for role, skus in (
            ("food_bowl", {"food_bowl"}),
            ("water_bowl", {"water_bowl"}),
        ):
            item = next(
                (
                    i
                    for i in items
                    if str(i.id) == data.get(role)
                    and i.sku in skus
                    and (i.x is not None)
                ),
                None,
            )
            if not item:
                raise ValueError("Posicione os dois potes antes de associar.")
            for other in await rows(session, QuintalCatCare, user_id):
                if other.cat_id != cat_id and other.state.get(role) == str(item.id):
                    raise ValueError("Este pote já pertence a outro gato.")
            free = walkable_cells(items, wallet.expansion_level)
            anchor = (wallet.cat_anchors or {}).get(target)
            goals = {
                (x + dx, y + dy)
                for x, y in item_cells(item.sku, item.x, item.y, item.rotation)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            } & free
            if not anchor or not reachable(tuple(anchor), goals, free, items):
                raise ValueError("O gato precisa alcançar os dois potes.")
            pair.append(str(item.id))
        s.update(food_bowl=pair[0], water_bowl=pair[1])
    else:
        if s.get("assisted") or not s.get("food_bowl") or (not s.get("water_bowl")):
            raise ValueError("Posicione os dois potes deste gato primeiro.")
        await validate_bowls(session, wallet, await rows(session, HabitatItem, user_id))
        if action == "water_cat":
            if s.get("water_day") == day(now):
                raise ValueError("Água deste gato já abastecida hoje.")
            s.update(water_day=day(now), water_until=iso(now + timedelta(hours=24)))
            await counter(session, user_id, "care", 1)
            await counter(session, user_id, "water_cat", 1, occurred_at=now)
            await counter(session, user_id, "care_day:" + day(now), 1)
        else:
            recipe, count = (data.get("recipe"), data.get("quantity", 1))
            if (
                recipe not in {"basic", *RECIPES}
                or type(count) is not int
                or (not 1 <= count <= 2)
            ):
                raise ValueError("Escolha uma ração e até duas porções.")
            refused = False
            if recipe != "basic":
                p = await preference(session, user_id, cat_id, recipe, rng)
                if (
                    p.state.get("known")
                    and p.state["level"] == 0
                    and (not data.get("confirm_refusal"))
                ):
                    raise ValueError(
                        "O gato odeia esta receita. Confirme a perda da porção."
                    )
                await stock(session, user_id, {"ration:" + recipe: -count})
                refused = p.state["level"] == 0
            else:
                if count != 1 or s.get("basic_day") == day(now):
                    raise ValueError("Uma porção básica por gato a cada dia.")
                s["basic_day"] = day(now)
            if s.get("portions", 0) and s.get("recipe") != "basic":
                await stock(session, user_id, {"ration:" + s["recipe"]: s["portions"]})
            s.update(recipe=recipe, portions=count, served_at=iso(now))
            if refused:
                s.update(
                    portions=0,
                    last_reaction={
                        "kind": "refused",
                        "recipe": recipe,
                        "at": iso(now),
                        "lost": count,
                        "portions_before": count,
                        "portions_after": 0,
                    },
                )
                s["last_reaction"]["event_id"] = record_reaction(
                    session, user_id, cat_id, s["last_reaction"]
                )
                await counter(session, user_id, "refusal", count)
    care.state = s
    if action == "serve":
        await meals(session, wallet, care, now, rng)
    return CommandOutcome(result, price)
