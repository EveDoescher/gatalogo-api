"""One command shares the coordinator transaction and wallet lock."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from app.persistence_models import HabitatWallet


@dataclass(frozen=True)
class ProductionCommand:
    session: AsyncSession
    user_id: UUID
    action: str
    target: str
    data: dict
    key: str
    now: datetime
    rng: Any
    wallet: HabitatWallet
    lv: dict
    price_for: Callable[[int], int]


@dataclass(frozen=True)
class CommandOutcome:
    result: dict
    price: int = 0
