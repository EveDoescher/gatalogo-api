"""Server-owned Habitat catalog and coin accounting."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence_models import HabitatItem, HabitatTransaction, HabitatWallet, User


GRID_SIZE = 8
MAX_PLACED_ITEMS = 24
MAX_VISIBLE_CATS = 3
EXPANSION_PRICES = (80, 180, 320)
LAYOUT_RULES_VERSION = 3
PATH_SKUS = {"stone_path", "pebble_path", "wood_path"}


def yard_cells(level: int) -> set[tuple[int, int]]:
    if level not in range(4):
        raise ValueError("Expansão inválida.")
    cells = {(x, y) for x in range(8) for y in range(8)}
    cells.update((x, -1) for x in range(2, 6))
    cells.update((-1, y) for y in range(2, 6))
    cells.update((8, y) for y in range(1, 7))
    cells.update((x, 8) for x in range(2, 7))
    for _ in range(level):
        cells |= {(x + dx, y + dy) for x, y in cells for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
    return cells


def item_cells(sku: str, x: int, y: int, rotation: int) -> set[tuple[int, int]]:
    definition = SHOP[sku]
    width, height = definition["width"], definition["height"]
    if rotation % 2:
        width, height = height, width
    return {(cx, cy) for cx in range(x, x + width) for cy in range(y, y + height)}

# The asset key is a Kenney isometric sprite stem, never a client supplied path.
SHOP: dict[str, dict] = {
    "cat_bed": {"name": "Caminha", "category": "descanso", "price": 0, "sprite": "bed", "width": 1, "height": 2, "max_owned": 1},
    "small_tree": {"name": "Árvore pequena", "category": "natureza", "price": 0, "sprite": "tree_default", "width": 1, "height": 1, "max_owned": 1},
    "small_bush": {"name": "Arbusto", "category": "natureza", "price": 0, "sprite": "plant_bushSmall", "width": 1, "height": 1, "max_owned": 1},
    "red_flowers": {"name": "Flores vermelhas", "category": "natureza", "price": 0, "sprite": "flower_redA", "width": 1, "height": 1, "max_owned": 1},
    "yellow_flowers": {"name": "Flores amarelas", "category": "natureza", "price": 20, "sprite": "flower_yellowA", "width": 1, "height": 1, "max_owned": 4},
    "soft_grass": {"name": "Grama macia", "category": "descanso", "price": 20, "sprite": "grass", "width": 1, "height": 1, "max_owned": 4},
    "small_rock": {"name": "Pedra", "category": "natureza", "price": 25, "sprite": "rock_smallA", "width": 1, "height": 1, "max_owned": 4},
    "stone_path": {"name": "Laje de pedra", "category": "caminhos", "price": 25, "sprite": "ground_pathTile", "width": 1, "height": 1, "max_owned": 6, "layer": "path"},
    "low_fence": {"name": "Cerca de madeira", "category": "natureza", "price": 30, "sprite": "fence_simple", "width": 1, "height": 1, "max_owned": 6},
    "leafy_plant": {"name": "Folhagem", "category": "natureza", "price": 35, "sprite": "plant_flatShort", "width": 1, "height": 1, "max_owned": 4},
    "floor_bed": {"name": "Cantinho de descanso", "category": "descanso", "price": 45, "sprite": "bed_floor", "width": 2, "height": 2, "max_owned": 2},
    "oak_tree": {"name": "Carvalho", "category": "natureza", "price": 60, "sprite": "tree_oak", "width": 2, "height": 2, "max_owned": 2},
    "large_log": {"name": "Tronco para brincar", "category": "brincadeira", "price": 65, "sprite": "log_large", "width": 2, "height": 1, "max_owned": 3},
    "large_rock": {"name": "Pedra de jardim", "category": "natureza", "price": 75, "sprite": "rock_largeA", "width": 2, "height": 2, "max_owned": 2},
    "detailed_tree": {"name": "Árvore frondosa", "category": "natureza", "price": 95, "sprite": "tree_detailed", "width": 2, "height": 2, "max_owned": 2},
    "tall_pine": {"name": "Pinheiro alto", "category": "natureza", "price": 120, "sprite": "tree_pineTallA", "width": 2, "height": 2, "max_owned": 2},
    "canvas_shelter": {"name": "Abrigo de lona", "category": "descanso", "price": 90, "sprite": "tent_smallOpen", "width": 2, "height": 2, "max_owned": 2},
    "canvas_retreat": {"name": "Refúgio de lona", "category": "descanso", "price": 140, "sprite": "tent_detailedOpen", "width": 3, "height": 2, "max_owned": 2},
    "green_cottage": {"name": "Casinha verde", "category": "casinhas", "price": 220, "sprite": "building-type-a", "width": 3, "height": 3, "max_owned": 1, "rotatable": False},
    "large_cottage": {"name": "Casinha ampla", "category": "casinhas", "price": 260, "sprite": "building-type-f", "width": 3, "height": 3, "max_owned": 1, "rotatable": False},
    "pebble_path": {"name": "Caminho de pedrinhas", "category": "caminhos", "price": 6, "sprite": "path_stone", "width": 1, "height": 1, "max_owned": 256, "layer": "path"},
    "wood_path": {"name": "Caminho de madeira", "category": "caminhos", "price": 8, "sprite": "path_wood", "width": 1, "height": 1, "max_owned": 256, "layer": "path"},
}

STARTER_SKUS = ("cat_bed", "small_tree", "small_bush", "red_flowers")
REWARDS = {"catalog": 25, "reference": 3, "sighting": 8, "post": 10, "comment": 3, "like": 1, "share": 3, "care": 3}
DAILY_LIMITS = {"reference": 2, "sighting": 3, "post": 2, "comment": 4, "like": 5, "share": 2, "care": 1}


async def wallet_for(session: AsyncSession, user_id: UUID) -> HabitatWallet:
    # A user row serializes both first-time creation and subsequent balance changes.
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())
    wallet = await session.get(HabitatWallet, user_id, with_for_update=True)
    if wallet is None:
        wallet = HabitatWallet(user_id=user_id, balance=0, layout_revision=0, selected_cat_ids=[], cat_anchors={}, expansion_level=0, layout_rules_version=LAYOUT_RULES_VERSION)
        session.add(wallet)
        for sku in STARTER_SKUS:
            session.add(HabitatItem(user_id=user_id, sku=sku))
        await session.flush()
    return wallet


async def grant_reward(
    session: AsyncSession,
    user_id: UUID,
    category: str,
    event_key: str,
    *,
    photo_hash: str | None = None,
) -> int:
    if category not in REWARDS:
        raise ValueError(f"Unknown Habitat reward: {category}")
    wallet = await wallet_for(session, user_id)
    existing = await session.scalar(select(HabitatTransaction.id).where(HabitatTransaction.user_id == user_id, HabitatTransaction.event_key == event_key))
    if existing is not None:
        return 0

    amount = REWARDS[category]
    if category == "catalog" and photo_hash:
        duplicate = await session.scalar(select(HabitatTransaction.id).where(HabitatTransaction.user_id == user_id, HabitatTransaction.category == "catalog", HabitatTransaction.photo_hash == photo_hash, HabitatTransaction.amount > 0).limit(1))
        if duplicate is not None:
            amount = 0
    limit = DAILY_LIMITS.get(category)
    if limit is not None:
        today = datetime.now(UTC).date()
        granted_today = await session.scalar(select(func.count()).select_from(HabitatTransaction).where(HabitatTransaction.user_id == user_id, HabitatTransaction.category == category, HabitatTransaction.amount > 0, HabitatTransaction.created_at >= datetime(today.year, today.month, today.day, tzinfo=UTC)))
        if (granted_today or 0) >= limit:
            amount = 0
    session.add(HabitatTransaction(user_id=user_id, event_key=event_key, category=category, amount=amount, photo_hash=photo_hash))
    wallet.balance += amount
    await session.flush()
    return amount


def validate_placements(items: list[HabitatItem], placements: list[dict], expansion_level: int = 0) -> None:
    if len(placements) > len(yard_cells(expansion_level)) + MAX_PLACED_ITEMS + 8 * expansion_level:
        raise ValueError("Há mais itens do que espaço no quintal.")
    owned = {str(item.id): item for item in items}
    seen: set[str] = set()
    solid: set[tuple[int, int]] = set()
    paths: set[tuple[int, int]] = set()
    solid_count = 0
    allowed = yard_cells(expansion_level)
    for placement in placements:
        item_id = placement["item_id"]
        if item_id not in owned or item_id in seen:
            raise ValueError("Item não pertence ao seu inventário.")
        seen.add(item_id)
        definition = SHOP[owned[item_id].sku]
        rotation = placement["rotation"]
        if rotation not in (0, 1, 2, 3):
            raise ValueError("Rotação inválida.")
        if not definition.get("rotatable", True) and rotation:
            raise ValueError("Este item não pode ser girado.")
        x, y = placement["x"], placement["y"]
        cells = item_cells(owned[item_id].sku, x, y, rotation)
        if not cells <= allowed:
            raise ValueError("Posição fora do gramado.")
        layer = paths if owned[item_id].sku in PATH_SKUS else solid
        if layer & cells:
            raise ValueError("Esta posição já está ocupada.")
        layer.update(cells)
        if owned[item_id].sku not in PATH_SKUS:
            solid_count += 1
    if solid_count > MAX_PLACED_ITEMS + 8 * expansion_level:
        raise ValueError("O limite de decorações do quintal foi atingido.")


def normalize_legacy_layout(wallet: HabitatWallet, items: list[HabitatItem]) -> bool:
    if wallet.layout_rules_version >= LAYOUT_RULES_VERSION:
        return False
    allowed = yard_cells(wallet.expansion_level)
    occupied = {"object": set(), "path": set()}
    moved = False
    placed = [item for item in items if item.x is not None and item.y is not None]
    placed.sort(key=lambda item: (-len(item_cells(item.sku, item.x, item.y, item.rotation)), str(item.id)))
    for item in placed:
        layer = "path" if item.sku in PATH_SKUS else "object"
        original = (item.x, item.y)
        candidates = sorted(allowed, key=lambda cell: (abs(cell[0] - original[0]) + abs(cell[1] - original[1]), cell[1], cell[0]))
        position = next((cell for cell in candidates if (footprint := item_cells(item.sku, *cell, item.rotation)) <= allowed and not footprint & occupied[layer]), None)
        if position is None:
            item.x = item.y = None
        else:
            item.x, item.y = position
            occupied[layer].update(item_cells(item.sku, *position, item.rotation))
        moved |= position != original
    anchors: dict[str, list[int]] = {}
    preferred = ((1, 3), (5, 2), (3, 6))
    for index, cat_id in enumerate(wallet.selected_cat_ids or []):
        start = (wallet.cat_anchors or {}).get(str(cat_id), preferred[index % len(preferred)])
        position = next((cell for cell in sorted(allowed, key=lambda cell: (abs(cell[0] - start[0]) + abs(cell[1] - start[1]), cell[1], cell[0])) if cell not in occupied["object"] and list(cell) not in anchors.values()), None)
        if position is not None:
            anchors[str(cat_id)] = list(position)
        moved |= position is None or list(position) != list(start)
    wallet.cat_anchors = anchors
    wallet.layout_rules_version = LAYOUT_RULES_VERSION
    wallet.layout_revision += 1
    return moved
