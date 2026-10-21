"""Server-clock production. Callers hold the wallet lock until commit/rollback."""

from datetime import timedelta
from app.quintal_models import QuintalPlot
from app.services.quintal_catalog import CROPS


from app.services.quintal_clock import utc
from app.services.quintal_inventory import counter, stock
from .context import ProductionCommand, CommandOutcome


async def plant(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    action = command.action
    target = command.target
    data = command.data
    now = command.now
    lv = command.lv
    result = {}
    price = 0
    try:
        slot = int(target)
    except ValueError as error:
        raise ValueError("Canteiro inválido.") from error
    plot = await session.get(QuintalPlot, (user_id, slot))
    if not plot:
        raise ValueError("Canteiro não liberado.")
    if action == "plant":
        crop = data.get("crop")
        if crop not in CROPS or CROPS[crop][1] > lv.get("garden", 0):
            raise ValueError("Cultivo não liberado.")
        if plot.crop:
            raise ValueError("Colha antes de plantar novamente.")
        plot.crop, plot.started_at, plot.ready_at, plot.watered = (
            crop,
            now,
            now + timedelta(seconds=CROPS[crop][2]),
            0,
        )
        await counter(session, user_id, "plant", 1, occurred_at=now)
    elif action == "water_crop":
        if not plot.crop or plot.watered or utc(plot.ready_at) <= now:
            raise ValueError("Este cultivo não precisa de rega.")
        plot.ready_at = now + (utc(plot.ready_at) - now) * 0.85
        plot.watered = 1
        await counter(session, user_id, "water_crop", 1, occurred_at=now)
    else:
        if not plot.crop or utc(plot.ready_at) > now:
            raise ValueError("Cultivo ainda não pronto.")
        crop = plot.crop
        await stock(session, user_id, {crop: CROPS[crop][3]})
        await counter(session, user_id, "harvest", 1, occurred_at=now)
        await counter(session, user_id, "crop:" + crop, 1)
        plot.crop, plot.started_at, plot.ready_at, plot.watered = (None, None, None, 0)
    return CommandOutcome(result, price)
