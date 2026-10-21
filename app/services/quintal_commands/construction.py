"""Server-clock production. Callers hold the wallet lock until commit/rollback."""

from datetime import timedelta
from app.quintal_models import QuintalFacility, QuintalPlot, QuintalResident
from app.services.habitat_pricing import effective_build_seconds
from app.services.quintal_catalog import FACILITIES


from app.services.quintal_clock import utc, iso
from app.services.quintal_inventory import counter, available_requirements
from app.services.quintal_simulation import resident_animals
from .context import ProductionCommand, CommandOutcome


async def build(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    target = command.target
    now = command.now
    wallet = command.wallet
    lv = command.lv
    result = {}
    price = 0
    definition = FACILITIES.get(target)
    if not definition:
        raise ValueError("Instalação inexistente.")
    kind, level_text = target.split(":")
    level = int(level_text)
    f = await session.get(QuintalFacility, (user_id, kind))
    values = await available_requirements(session, wallet)
    if (
        level != lv.get(kind, 0) + 1
        or wallet.expansion_level < definition[3]
        or any((values.get(k, 0) < v for k, v in definition[4].items()))
    ):
        raise ValueError("Requisitos da instalação ainda não atendidos.")
    if f and f.target_level:
        raise ValueError("Esta instalação já tem uma obra em andamento.")
    price = command.price_for(definition[1])
    if wallet.balance < price:
        raise ValueError("Moedas insuficientes.")
    if not f:
        f = QuintalFacility(user_id=user_id, kind=kind, level=0)
        session.add(f)
    f.target_level, f.ready_at = (
        level,
        now + timedelta(seconds=effective_build_seconds(definition[2])),
    )
    f.started_at = now
    return CommandOutcome(result, price)


async def finish_build(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    target = command.target
    now = command.now
    result = {}
    price = 0
    f = await session.get(QuintalFacility, (user_id, target))
    if not f or not f.target_level or utc(f.ready_at) > now:
        raise ValueError("A obra ainda não está pronta.")
    f.level, f.target_level, f.ready_at = (f.target_level, 0, None)
    f.started_at = None
    if target == "pier" and (not await session.get(QuintalFacility, (user_id, "rod"))):
        session.add(
            QuintalFacility(user_id=user_id, kind="rod", level=1, target_level=0)
        )
    if target == "garden":
        for slot in range(f.level * 4):
            if not await session.get(QuintalPlot, (user_id, slot)):
                session.add(QuintalPlot(user_id=user_id, slot=slot, watered=0))
    if target in ("coop", "pasture", "orchard"):
        resident = await session.get(QuintalResident, (user_id, target))
        if not resident:
            resident = QuintalResident(user_id=user_id, kind=target, state={})
            session.add(resident)
        resident.state = {
            **resident.state,
            "count": 2 * f.level
            if target == "coop"
            else f.level
            if target == "pasture"
            else 2,
            "cursor": iso(now),
        }
        state = {**resident.state, "animals": resident.state.get("animals", [])}
        resident.state = {**state, "animals": resident_animals(state, now)}
    await counter(session, user_id, "facility", 1, occurred_at=now)
    return CommandOutcome(result, price)
