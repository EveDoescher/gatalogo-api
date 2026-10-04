"""Habitat inventory, layout and virtual currency endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_database_session
from app.persistence_models import FeedItem, HabitatItem, HabitatTransaction, MissingReport, RemoteCat, User
from app.routers.auth import current_user
from app.services.habitat_service import BATCH_SKUS, DAILY_LIMITS, EXPANSION_PRICES, REWARDS, SHOP, grant_reward, normalize_legacy_layout, validate_placements, wallet_for, walkable_cells, yard_cells
from app.services.habitat_interactions import care_summary, interact


router = APIRouter(prefix="/app/habitat", tags=["habitat"])


class PurchaseRequest(BaseModel):
    sku: str
    idempotency_key: UUID
    quantity: int = Field(default=1, ge=1, le=20)


class ExpansionRequest(BaseModel):
    expected_level: int = Field(ge=0, le=2)
    idempotency_key: UUID


class PlacementRequest(BaseModel):
    item_id: UUID
    x: int = Field(ge=-4, le=11)
    y: int = Field(ge=-4, le=11)
    rotation: int = Field(ge=0, le=3)


class LayoutRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    placements: list[PlacementRequest] = Field(max_length=1024)
    selected_cat_ids: list[UUID] = Field(max_length=6)
    cat_anchors: dict[str, list[int]] | None = None


class HabitatActionRequest(BaseModel):
    action: str
    target_type: str | None = None
    target_id: UUID | None = None


class InteractionRequest(BaseModel):
    action: Literal["pet", "feed", "treat", "water"]
    target_id: UUID
    bowl_id: UUID | None = None
    idempotency_key: UUID


@router.post("/interactions")
async def interaction(payload: InteractionRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    try:
        await interact(session, user.id, payload.action, payload.target_id, payload.idempotency_key, payload.bowl_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    result = await _snapshot(session, user.id)
    await session.commit()
    return result


def _shop() -> list[dict]:
    return [{"sku": sku, **definition} for sku, definition in SHOP.items()]


async def _snapshot(session: AsyncSession, user_id: UUID) -> dict:
    wallet = await wallet_for(session, user_id)
    items = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user_id).order_by(HabitatItem.created_at, HabitatItem.id))).scalars().all()
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user_id, RemoteCat.deleted_at.is_(None)))).scalars().all()
    pets = [
        {
            "id": str(cat.client_id),
            "name": (cat.payload or {}).get("name") or "Meu gato",
            "primary_color": (cat.analysis or {}).get("primary_color"),
        }
        for cat in cats if (cat.payload or {}).get("is_owned")
    ]
    return {
        "balance": wallet.balance,
        "revision": wallet.layout_revision,
        "layout_rules_version": wallet.layout_rules_version,
        "selected_cat_ids": wallet.selected_cat_ids or [],
        "cat_anchors": wallet.cat_anchors or {},
        "expansion_level": wallet.expansion_level,
        "cat_capacity": 3 + wallet.expansion_level,
        "decoration_capacity": 24 + 8 * wallet.expansion_level,
        "buildable_cells": [list(cell) for cell in sorted(yard_cells(wallet.expansion_level))],
        "next_expansion_price": EXPANSION_PRICES[wallet.expansion_level] if wallet.expansion_level < 3 else None,
        "items": [_item_response(item) for item in items],
        "care": care_summary(wallet.care_state or {}, 3 + wallet.expansion_level, datetime.now(UTC)),
        "cats": pets,
        "shop_version": 4,
    }


@router.get("")
async def get_habitat(user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    wallet = await wallet_for(session, user.id)
    owned_items = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user.id))).scalars().all()
    adjusted = normalize_legacy_layout(wallet, owned_items)
    result = await _snapshot(session, user.id)
    result["layout_adjusted"] = adjusted
    await session.commit()
    return result


@router.get("/shop")
async def get_shop() -> dict:
    return {"version": 4, "items": _shop(), "rewards": REWARDS, "daily_limits": DAILY_LIMITS, "expansion_prices": EXPANSION_PRICES}


@router.post("/purchases")
async def purchase(payload: PurchaseRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    definition = SHOP.get(payload.sku)
    if definition is None or definition["price"] <= 0:
        raise HTTPException(422, "Item não disponível para compra.")
    if payload.quantity != 1 and payload.sku not in BATCH_SKUS:
        raise HTTPException(422, "A compra em quantidade é exclusiva dos caminhos e terrenos.")
    wallet = await wallet_for(session, user.id)
    key = str(payload.idempotency_key)
    previous = await session.scalar(select(HabitatTransaction).where(HabitatTransaction.user_id == user.id, HabitatTransaction.event_key == f"purchase:{key}"))
    if previous is not None:
        existing = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user.id, HabitatItem.purchase_key.like(f"{key}:%")))).scalars().all()
        if not existing:
            existing = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user.id, HabitatItem.purchase_key == key))).scalars().all()
        existing.sort(key=lambda item: item.purchase_key or "")
        if len(existing) != payload.quantity or any(item.sku != payload.sku for item in existing):
            raise HTTPException(409, "Esta chave já foi usada para outro item.")
        serialized = [_item_response(item) for item in existing]
        result = {"item": serialized[0], "items": serialized, "balance": wallet.balance}
        await session.commit()
        return result
    owned_count = len((await session.execute(select(HabitatItem.id).where(HabitatItem.user_id == user.id, HabitatItem.sku == payload.sku))).all())
    if owned_count + payload.quantity > definition["max_owned"]:
        raise HTTPException(409, "Você já possui o limite deste item.")
    price = definition["price"] * payload.quantity
    if wallet.balance < price:
        raise HTTPException(409, "Moedas insuficientes para esta compra.")
    items = [HabitatItem(id=uuid4(), user_id=user.id, sku=payload.sku, purchase_key=f"{key}:{index}") for index in range(payload.quantity)]
    session.add_all(items)
    session.add(HabitatTransaction(user_id=user.id, event_key=f"purchase:{key}", category="purchase", amount=-price))
    wallet.balance -= price
    await session.commit()
    serialized = [_item_response(item) for item in items]
    return {"item": serialized[0], "items": serialized, "balance": wallet.balance}


def _item_response(item: HabitatItem) -> dict:
    return {"id": str(item.id), "sku": item.sku, "x": item.x, "y": item.y, "rotation": item.rotation, "care_state": item.care_state or {}}


@router.post("/expansions")
async def expand_yard(payload: ExpansionRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    wallet = await wallet_for(session, user.id)
    key = str(payload.idempotency_key)
    previous = await session.scalar(select(HabitatTransaction).where(HabitatTransaction.user_id == user.id, HabitatTransaction.event_key == f"expansion:{key}"))
    if previous is not None:
        if previous.category != f"expansion:{payload.expected_level + 1}":
            raise HTTPException(409, "Esta chave já foi usada para outra expansão.")
        result = await _snapshot(session, user.id)
        await session.commit()
        return result
    if wallet.expansion_level != payload.expected_level or wallet.expansion_level >= 3:
        raise HTTPException(409, "Atualize o Habitat antes de ampliar o quintal.")
    price = EXPANSION_PRICES[wallet.expansion_level]
    if wallet.balance < price:
        raise HTTPException(409, "Moedas insuficientes para ampliar o quintal.")
    wallet.balance -= price
    wallet.expansion_level += 1
    wallet.layout_revision += 1
    session.add(HabitatTransaction(user_id=user.id, event_key=f"expansion:{key}", category=f"expansion:{wallet.expansion_level}", amount=-price))
    result = await _snapshot(session, user.id)
    await session.commit()
    return result


@router.put("/layout")
async def save_layout(payload: LayoutRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    wallet = await wallet_for(session, user.id)
    items = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user.id).with_for_update())).scalars().all()
    normalize_legacy_layout(wallet, items)
    if wallet.layout_revision != payload.expected_revision:
        raise HTTPException(409, "O Habitat foi alterado em outro dispositivo.")
    if len(set(payload.selected_cat_ids)) != len(payload.selected_cat_ids):
        raise HTTPException(422, "Selecione cada gato apenas uma vez.")
    cats = (await session.execute(select(RemoteCat).where(RemoteCat.user_id == user.id, RemoteCat.client_id.in_(payload.selected_cat_ids), RemoteCat.deleted_at.is_(None)))).scalars().all() if payload.selected_cat_ids else []
    if len(payload.selected_cat_ids) > 3 + wallet.expansion_level or len(cats) != len(payload.selected_cat_ids) or any(not (cat.payload or {}).get("is_owned") for cat in cats):
        raise HTTPException(422, "Selecione apenas gatos cadastrados em Meus Gatos.")
    placements = [item.model_dump(mode="json") for item in payload.placements]
    try:
        validate_placements(items, placements, wallet.expansion_level)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    positions = {str(entry.item_id): entry for entry in payload.placements}
    allowed = yard_cells(wallet.expansion_level)
    solid_cells = allowed - walkable_cells(items, wallet.expansion_level, placements)
    requested_anchors = payload.cat_anchors if payload.cat_anchors is not None else {key: value for key, value in (wallet.cat_anchors or {}).items() if key in {str(cat_id) for cat_id in payload.selected_cat_ids}}
    selected = {str(cat_id) for cat_id in payload.selected_cat_ids}
    if set(requested_anchors) - selected:
        raise HTTPException(422, "Posição de gato não selecionado.")
    anchors: dict[str, list[int]] = {}
    used: set[tuple[int, int]] = set()
    for cat_id in (str(value) for value in payload.selected_cat_ids):
        raw = requested_anchors.get(cat_id)
        if raw is not None:
            if len(raw) != 2 or not all(isinstance(value, int) for value in raw):
                raise HTTPException(422, "Posição de gato inválida.")
            candidate = (raw[0], raw[1])
            if candidate not in allowed or candidate in solid_cells or candidate in used:
                raise HTTPException(422, "Posição de gato ocupada ou fora do quintal.")
        else:
            candidate = next((cell for cell in sorted(allowed) if cell not in solid_cells and cell not in used), None)
            if candidate is None:
                raise HTTPException(422, "Sem espaço para este gato.")
        used.add(candidate)
        anchors[cat_id] = list(candidate)
    for item in items:
        entry = positions.get(str(item.id))
        item.x = entry.x if entry else None
        item.y = entry.y if entry else None
        item.rotation = entry.rotation if entry else 0
    wallet.selected_cat_ids = [str(cat_id) for cat_id in payload.selected_cat_ids]
    wallet.cat_anchors = anchors
    wallet.layout_revision += 1
    result = await _snapshot(session, user.id)
    await session.commit()
    return result


@router.post("/actions")
async def record_action(payload: HabitatActionRequest, user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    # Retired UI actions stay harmless for older app versions.
    wallet = await wallet_for(session, user.id)
    await session.commit()
    return {"earned": 0, "balance": wallet.balance}



@router.get("/transactions")
async def transactions(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), user: User = Depends(current_user), session: AsyncSession = Depends(get_database_session)) -> dict:
    rows = (await session.execute(select(HabitatTransaction).where(HabitatTransaction.user_id == user.id, HabitatTransaction.amount != 0).order_by(HabitatTransaction.created_at.desc(), HabitatTransaction.id.desc()).offset(offset).limit(limit))).scalars().all()
    return {"items": [{"id": str(row.id), "category": row.category, "amount": row.amount, "created_at": row.created_at.isoformat()} for row in rows]}
