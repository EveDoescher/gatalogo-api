from app.services.quintal_commands import ProductionCommand, execute

"""Server-clock production. Callers hold the wallet lock until commit/rollback."""
from datetime import UTC, datetime
import random
from sqlalchemy import select
from app.persistence_models import HabitatTransaction
from app.services.habitat_service import wallet_for
from app.services.habitat_pricing import effective_price

from app.services.quintal_clock import utc as utc, day as day, iso as iso
from app.services.quintal_inventory import (
    rows as rows,
    counter as counter,
    stock as stock,
    levels as levels,
    available_requirements as available_requirements,
)
from app.services.quintal_care import (
    cat_care as cat_care,
    preference as preference,
    evolve as evolve,
    bond as bond,
    play_care_bonus as play_care_bonus,
    meals as meals,
    ensure_pair as ensure_pair,
    validate_bowls as validate_bowls,
    owned_cat as owned_cat,
)
from app.services.quintal_simulation import (
    resident_animals as resident_animals,
    reconcile as reconcile,
)
from app.services.quintal_projection import (
    resident_projection as resident_projection,
    projection as projection,
)
from app.services.quintal_clock import CALENDAR as CALENDAR
from app.services.quintal_inventory import PROGRESS_METRICS as PROGRESS_METRICS


async def operation(session, user_id, action, target, data, key, now=None, rng=None):
    now, rng = now or datetime.now(UTC), rng or random.SystemRandom()
    wallet = await wallet_for(session, user_id)
    event_key = f"quintal:{key}"
    fingerprint = {"action": action, "target": target, "data": data}
    old = await session.scalar(
        select(HabitatTransaction).where(
            HabitatTransaction.user_id == user_id,
            HabitatTransaction.event_key == event_key,
        )
    )
    if old:
        if old.details.get("request") != fingerprint:
            raise ValueError("Chave já utilizada para outra operação.")
        return old.details.get("result", {})
    await reconcile(session, wallet, now, rng)
    result, price = {}, 0
    lv = await levels(session, user_id)
    outcome = await execute(
        ProductionCommand(
            session,
            user_id,
            action,
            target,
            data,
            key,
            now,
            rng,
            wallet,
            lv,
            effective_price,
        )
    )
    result, price = outcome.result, outcome.price
    wallet.balance -= price
    wallet.layout_revision += 1
    session.add(
        HabitatTransaction(
            user_id=user_id,
            event_key=event_key,
            category="quintal",
            amount=-price,
            details={"request": fingerprint, "result": result},
        )
    )
    await session.flush()
    return result
