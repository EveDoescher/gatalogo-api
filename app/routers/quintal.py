"""Production endpoints share the Quintal wallet transaction/ownership boundary."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
import math
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_database_session
from app.persistence_models import User
from app.dependencies import current_user
from app.services.quintal_production import operation
from app.services.quintal_projection import projection
from app.services.quintal_games import session_operation
from app.services import progression as progress
from app.services.quintal_progress import publish_progress
from app.services.realtime_events import publish_committed_events
from app.services.quintal_notifications import notify_ready

from app.services.habitat_presenter import habitat_snapshot

router = APIRouter(prefix="/app/quintal", tags=["quintal"])

class OperationRequest(BaseModel):
    action: Literal["build", "finish_build", "plant", "water_crop", "harvest", "cook", "order", "collect", "cancel", "feed_residents", "water_residents", "collect_residents", "assign_bowls", "serve", "water_cat", "add_cat", "remove_cat", "start_fish", "finish_fish", "start_play", "finish_play", "ack_return", "ack_rewards", "rename_resident"]
    target: str = Field(max_length=64)
    data: dict = Field(default_factory=dict)
    idempotency_key: UUID

    @field_validator("data")
    @classmethod
    def bounded_data(cls, value):
        allowed = {"crop", "recipe", "payment", "product_option", "quantity", "confirm_refusal", "anchor", "food", "water", "food_bowl", "water_bowl", "trace", "slot", "name", "resident_id", "event_ids"}
        if 'event_ids' in value:
            ids = value['event_ids']
            if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(k, str) or len(k) > 160 for k in ids):
                raise ValueError('Eventos de retorno inválidos.')
        if len(value) > 8 or set(value) - allowed: raise ValueError("Parâmetros inválidos.")
        for key in ("crop", "recipe", "payment", "product_option", "food_bowl", "water_bowl", "name", "resident_id"):
            if key in value and (not isinstance(value[key], str) or len(value[key]) > 64): raise ValueError("Texto inválido.")
        for key in ("quantity", "slot"):
            if key in value and type(value[key]) is not int: raise ValueError("Quantidade inválida.")
        if "confirm_refusal" in value and type(value["confirm_refusal"]) is not bool: raise ValueError("Confirmação inválida.")
        for key in ("anchor", "food", "water"):
            if key in value and (not isinstance(value[key], list) or len(value[key]) != 2 or any(type(n) is not int or not -4 <= n <= 11 for n in value[key])): raise ValueError("Posição inválida.")
        if "trace" in value:
            trace = value["trace"]
            if not isinstance(trace, list) or not 16 <= len(trace) <= 200: raise ValueError("Interação inválida.")
            for point in trace:
                if not isinstance(point, dict) or set(point) not in ({"t", "position"}, {"t", "position", "y"}): raise ValueError("Interação inválida.")
                for key, limit in (("t", 30), ("position", 1), *(([("y", 1)]) if "y" in point else [])):
                    if type(point[key]) not in (int, float) or not math.isfinite(point[key]) or not 0 <= point[key] <= limit: raise ValueError("Interação inválida.")
        return value

@router.get("")
async def get_production(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    await progress.account(session, user.id)
    result = await projection(session, user.id)
    rewards = await publish_progress(session, user.id)
    await notify_ready(session, user.id, result)
    habitat = await habitat_snapshot(session, user.id)
    await session.commit()
    await publish_committed_events(session)
    return {"production": result, "habitat": habitat, "progress": rewards}

@router.post("/operations")
async def mutate(payload: OperationRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)):
    try:
        await progress.account(session, user.id)
        runner = session_operation if payload.action.startswith(("start_", "finish_fish", "finish_play")) else operation
        outcome = await runner(session, user.id, payload.action, payload.target, payload.data, payload.idempotency_key)
        result = await projection(session, user.id)
        rewards = await publish_progress(session, user.id)
        await notify_ready(session, user.id, result)
        habitat = await habitat_snapshot(session, user.id)
        await session.commit()
        await publish_committed_events(session)
        return {"outcome": outcome, "production": result, "habitat": habitat, "progress": rewards}
    except ValueError as error:
        await session.rollback()
        raise HTTPException(409, str(error)) from error
