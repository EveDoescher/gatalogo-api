"""Server-clock production. Callers hold the wallet lock until commit/rollback."""

from app.services.quintal_rewards import acknowledge_rewards
from app.services.quintal_return import acknowledge


from .context import ProductionCommand, CommandOutcome


async def ack_rewards(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    data = command.data
    result = {}
    price = 0
    result["acknowledged"] = await acknowledge_rewards(
        session, user_id, data.get("event_ids", [])
    )
    return CommandOutcome(result, price)


async def ack_return(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    data = command.data
    result = {}
    price = 0
    result["acknowledged"] = await acknowledge(
        session, user_id, data.get("event_ids", [])
    )
    return CommandOutcome(result, price)
