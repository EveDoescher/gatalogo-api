"""Dispatch domain rules without committing the shared transaction."""

from .context import ProductionCommand as ProductionCommand
from .receipts import ack_rewards
from .receipts import ack_return
from .residents import rename_resident
from .construction import build
from .construction import finish_build
from .garden import plant
from .tasks import cook
from .tasks import collect
from .residents import feed_residents
from .cats import add_cat
from .cats import assign_bowls

_HANDLERS = {
    "ack_rewards": ack_rewards,
    "ack_return": ack_return,
    "rename_resident": rename_resident,
    "build": build,
    "finish_build": finish_build,
    "plant": plant,
    "water_crop": plant,
    "harvest": plant,
    "cook": cook,
    "order": cook,
    "collect": collect,
    "cancel": collect,
    "feed_residents": feed_residents,
    "water_residents": feed_residents,
    "collect_residents": feed_residents,
    "add_cat": add_cat,
    "remove_cat": add_cat,
    "assign_bowls": assign_bowls,
    "serve": assign_bowls,
    "water_cat": assign_bowls,
}


async def execute(command: ProductionCommand):
    handler = _HANDLERS.get(command.action)
    if handler is None:
        raise ValueError("Operação desconhecida.")
    return await handler(command)
