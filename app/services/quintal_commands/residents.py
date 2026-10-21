"""Server-clock production. Callers hold the wallet lock until commit/rollback."""

from copy import deepcopy
from datetime import timedelta
from app.quintal_models import QuintalResident


from app.services.quintal_clock import utc, day, iso
from app.services.quintal_inventory import counter, stock
from app.services.quintal_simulation import resident_animals
from .context import ProductionCommand, CommandOutcome


async def rename_resident(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    target = command.target
    data = command.data
    result = {}
    price = 0
    resident = await session.get(QuintalResident, (user_id, target))
    slot, name = (data.get("slot"), data.get("name"))
    if (
        not resident
        or type(slot) is not int
        or (not 0 <= slot < resident.state.get("count", 0))
        or (not isinstance(name, str))
        or (not 1 <= len(name.strip()) <= 32)
    ):
        raise ValueError("Informe um nome de até 32 caracteres para um residente seu.")
    resident.state = {
        **resident.state,
        "names": {**resident.state.get("names", {}), str(slot): name.strip()},
    }
    return CommandOutcome(result, price)


async def feed_residents(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    action = command.action
    target = command.target
    data = command.data
    now = command.now
    result = {}
    price = 0
    resident = await session.get(QuintalResident, (user_id, target))
    if not resident:
        raise ValueError("Espaço não liberado.")
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
            if not selected:
                raise ValueError("Residente não encontrado neste espaço.")
        quantity = sum((a.get("ready", 0) for a in selected))
        if not quantity:
            raise ValueError("Ainda não há produção pronta.")
        if target == "orchard":
            outputs = {
                "apple": sum((a["ready"] for a in selected if a["slot"] % 2 == 0)),
                "pear": sum((a["ready"] for a in selected if a["slot"] % 2 == 1)),
            }
        else:
            outputs = {"egg" if target == "coop" else "milk": quantity}
        await stock(session, user_id, outputs)
        for a in selected:
            a.update(ready=0, cursor=iso(now))
        s["ready"] = sum((a.get("ready", 0) for a in animals))
        s["cursor"] = iso(now)
        result = {
            "collected": {k: v for k, v in outputs.items() if v},
            "resident_id": resident_id,
        }
        await counter(
            session,
            user_id,
            "egg" if target == "coop" else "milk" if target == "pasture" else "fruit",
            quantity,
            occurred_at=now,
        )
        if target == "pasture":
            await counter(session, user_id, "milk_collection", 1, occurred_at=now)
    elif target == "orchard":
        raise ValueError("Fruteiras não usam comedouro ou bebedouro.")
    elif action == "feed_residents":
        hungry = [
            a
            for a in animals
            if not utc(a.get("food_until")) or utc(a["food_until"]) <= now
        ]
        if not hungry:
            raise ValueError("O comedouro ainda está abastecido.")
        count = len(hungry)
        await stock(
            session,
            user_id,
            {"corn": -count}
            if target == "coop"
            else {"wheat": -2 * count, "corn": -count},
        )
        for a in hungry:
            a.update(food_start=iso(now), food_until=iso(now + timedelta(hours=24)))
        s["fed_count"] = len(animals)
    else:
        if s.get("water_day") == day(now):
            raise ValueError("Água já abastecida hoje.")
        s.update(
            water_day=day(now),
            water_start=iso(now),
            water_until=iso(now + timedelta(hours=24)),
        )
    resident.state = {**s, "animals": animals}
    return CommandOutcome(result, price)
