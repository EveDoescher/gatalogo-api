"""Server-owned Quintal catalog and coin accounting."""

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
GROUND_LAYERS = {"path", "terrain"}


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
    "cat_bed": {'name': 'Caminha', 'category': 'descanso', 'price': 0, 'sprite': 'bed', 'width': 1, 'height': 2, 'max_owned': 1},
    "small_tree": {'name': 'Árvore pequena', 'category': 'natureza', 'price': 0, 'sprite': 'tree_default', 'width': 1, 'height': 1, 'max_owned': 1},
    "small_bush": {'name': 'Arbusto', 'category': 'natureza', 'price': 0, 'sprite': 'plant_bushSmall', 'width': 1, 'height': 1, 'max_owned': 1},
    "red_flowers": {'name': 'Flores vermelhas', 'category': 'natureza', 'price': 0, 'sprite': 'flower_redA', 'width': 1, 'height': 1, 'max_owned': 1},
    "yellow_flowers": {'name': 'Flores amarelas', 'category': 'natureza', 'price': 20, 'sprite': 'flower_yellowA', 'width': 1, 'height': 1, 'max_owned': 4},
    "soft_grass": {'name': 'Grama macia', 'category': 'descanso', 'price': 20, 'sprite': 'grass', 'width': 1, 'height': 1, 'max_owned': 4},
    "small_rock": {'name': 'Pedra', 'category': 'natureza', 'price': 25, 'sprite': 'rock_smallA', 'width': 1, 'height': 1, 'max_owned': 4},
    "stone_path": {'name': 'Laje de pedra', 'category': 'caminhos', 'price': 25, 'sprite': 'ground_pathTile', 'width': 1, 'height': 1, 'max_owned': 6, 'layer': 'path'},
    "low_fence": {'name': 'Cerca de madeira', 'category': 'natureza', 'price': 30, 'sprite': 'fence_simple', 'width': 1, 'height': 1, 'max_owned': 6},
    "leafy_plant": {'name': 'Folhagem', 'category': 'natureza', 'price': 35, 'sprite': 'plant_flatShort', 'width': 1, 'height': 1, 'max_owned': 4},
    "floor_bed": {'name': 'Cantinho de descanso', 'category': 'descanso', 'price': 35, 'sprite': 'bed_floor', 'width': 2, 'height': 2, 'max_owned': 2},
    "oak_tree": {'name': 'Carvalho', 'category': 'natureza', 'price': 45, 'sprite': 'tree_oak', 'width': 2, 'height': 2, 'max_owned': 2},
    "large_log": {'name': 'Tronco para brincar', 'category': 'brincadeira', 'price': 40, 'sprite': 'log_large', 'width': 2, 'height': 1, 'max_owned': 3},
    "large_rock": {'name': 'Pedra de jardim', 'category': 'natureza', 'price': 45, 'sprite': 'rock_largeA', 'width': 2, 'height': 2, 'max_owned': 2},
    "detailed_tree": {'name': 'Árvore frondosa', 'category': 'natureza', 'price': 60, 'sprite': 'tree_detailed', 'width': 2, 'height': 2, 'max_owned': 2},
    "tall_pine": {'name': 'Pinheiro alto', 'category': 'natureza', 'price': 70, 'sprite': 'tree_pineTallA', 'width': 2, 'height': 2, 'max_owned': 2},
    "canvas_shelter": {'name': 'Abrigo de lona', 'category': 'descanso', 'price': 55, 'sprite': 'tent_smallOpen', 'width': 2, 'height': 2, 'max_owned': 2},
    "canvas_retreat": {'name': 'Refúgio de lona', 'category': 'descanso', 'price': 85, 'sprite': 'tent_detailedOpen', 'width': 3, 'height': 2, 'max_owned': 2},
    "green_cottage": {'name': 'Casinha verde', 'category': 'casinhas', 'price': 120, 'sprite': 'building-type-a', 'width': 3, 'height': 3, 'max_owned': 1, 'rotatable': False},
    "large_cottage": {'name': 'Casinha ampla', 'category': 'casinhas', 'price': 150, 'sprite': 'building-type-f', 'width': 3, 'height': 3, 'max_owned': 1, 'rotatable': False},
    "pebble_path": {'name': 'Caminho de pedrinhas', 'category': 'caminhos', 'price': 6, 'sprite': 'path_stone', 'width': 1, 'height': 1, 'max_owned': 256, 'layer': 'path'},
    "wood_path": {'name': 'Caminho de madeira', 'category': 'caminhos', 'price': 8, 'sprite': 'path_wood', 'width': 1, 'height': 1, 'max_owned': 256, 'layer': 'path'},
    "food_bowl": {'name': 'Pote de ração', 'category': 'descanso', 'price': 15, 'sprite': 'bowl', 'width': 1, 'height': 1, 'max_owned': 12},
    "water_bowl": {'name': 'Pote de água', 'category': 'descanso', 'price': 15, 'sprite': 'bowl', 'width': 1, 'height': 1, 'max_owned': 12},
    "play_crate": {'name': 'Caixa de brincar', 'category': 'brincadeira', 'price': 25, 'sprite': 'crate', 'width': 1, 'height': 1, 'max_owned': 4},
    "wood_platform": {'name': 'Deck de madeira', 'category': 'descanso', 'price': 45, 'sprite': 'platform-planks', 'width': 2, 'height': 2, 'max_owned': 2},
    "garden_planter": {'name': 'Jardineira', 'category': 'natureza', 'price': 30, 'sprite': 'planter', 'width': 1, 'height': 1, 'max_owned': 4},
    "round_stump": {'name': 'Toco de árvore', 'category': 'brincadeira', 'price': 25, 'sprite': 'stump_roundDetailed', 'width': 1, 'height': 1, 'max_owned': 4},
    "closed_tent": {'name': 'Barraca aconchegante', 'category': 'descanso', 'price': 55, 'sprite': 'tent_smallClosed', 'width': 2, 'height': 2, 'max_owned': 2},
    "purple_flowers": {'name': 'Flores roxas', 'category': 'natureza', 'price': 20, 'sprite': 'flower_purpleA', 'width': 1, 'height': 1, 'max_owned': 4},
    "garden_bush": {'name': 'Arbusto frondoso', 'category': 'natureza', 'price': 25, 'sprite': 'plant_bushDetailed', 'width': 1, 'height': 1, 'max_owned': 4},
    "red_mushrooms": {'name': 'Cogumelos vermelhos', 'category': 'natureza', 'price': 20, 'sprite': 'mushroom_redGroup', 'width': 1, 'height': 1, 'max_owned': 4},
    "garden_pot": {'name': 'Vaso de jardim', 'category': 'natureza', 'price': 15, 'sprite': 'pot_small', 'width': 1, 'height': 1, 'max_owned': 4},
    "palm_tree": {'name': 'Palmeira', 'category': 'natureza', 'price': 60, 'sprite': 'tree_palmBend', 'width': 2, 'height': 2, 'max_owned': 2},
    "autumn_tree": {'name': 'Árvore de outono', 'category': 'natureza', 'price': 40, 'sprite': 'tree_small_fall', 'width': 1, 'height': 1, 'max_owned': 4},
    "solar_cottage": {'name': 'Casinha solar', 'category': 'casinhas', 'price': 120, 'sprite': 'building-type-b', 'width': 3, 'height': 3, 'max_owned': 2},
    "cozy_cottage": {'name': 'Casinha acolhedora', 'category': 'casinhas', 'price': 130, 'sprite': 'building-type-c', 'width': 3, 'height': 3, 'max_owned': 2},
    "friendship_garden": {"name": "Flores da amizade", "category": "natureza", "price": 0, "sprite": "flower_purpleA", "width": 1, "height": 1, "max_owned": 1},
    "leaf_garden": {"name": "Canteiro de folhas", "category": "jardim", "price": 20, "sprite": "crops_leafsStageA", "width": 1, "height": 1, "max_owned": 4},
    "bamboo_garden": {"name": "Canteiro de bambu", "category": "jardim", "price": 30, "sprite": "crops_bambooStageA", "width": 1, "height": 1, "max_owned": 4},
    "water_tile": {"name": "Lago", "category": "terreno", "price": 5, "sprite": "water", "width": 1, "height": 1, "max_owned": 256, "layer": "terrain", "rotatable": False},
    "earth_tile": {"name": "Terra", "category": "terreno", "price": 3, "sprite": "dirt", "width": 1, "height": 1, "max_owned": 256, "layer": "terrain", "rotatable": False},
    "wood_bridge": {"name": "Ponte de madeira", "category": "terreno", "price": 35, "sprite": "bridge_wood", "width": 2, "height": 2, "max_owned": 4, "layer": "bridge"},
    "stone_bridge": {"name": "Ponte de pedra", "category": "terreno", "price": 45, "sprite": "bridge_stoneRound", "width": 2, "height": 2, "max_owned": 4, "layer": "bridge"},
    "water_lily": {"name": "Nenúfar", "category": "terreno", "price": 15, "sprite": "lily_large", "width": 1, "height": 1, "max_owned": 8, "layer": "water_decor"},
}

PATH_SKUS = {sku for sku, item in SHOP.items() if item.get("layer") == "path"}
BATCH_SKUS = {sku for sku, item in SHOP.items() if item.get("layer") in GROUND_LAYERS}


def walkable_cells(items: list[HabitatItem], level: int, placements: list[dict] | None = None) -> set[tuple[int, int]]:
    owned = {str(item.id): item for item in items}
    placements = placements if placements is not None else [
        {"item_id": str(item.id), "x": item.x, "y": item.y, "rotation": item.rotation}
        for item in items if item.x is not None and item.y is not None]
    blocked, bridges = set(), set()
    for entry in placements:
        sku = owned[entry["item_id"]].sku
        layer = SHOP[sku].get("layer", "solid")
        cells = item_cells(sku, entry["x"], entry["y"], entry["rotation"])
        if layer == "bridge":
            bridges.update(cells)
        elif layer not in GROUND_LAYERS or sku == "water_tile":
            blocked.update(cells)
    return (yard_cells(level) - blocked) | bridges


def can_cross_bridge(start: tuple[int, int], end: tuple[int, int], items: list[HabitatItem]) -> bool:
    for item in items:
        if item.x is None or SHOP[item.sku].get("layer") != "bridge":
            continue
        cells = item_cells(item.sku, item.x, item.y, item.rotation)
        if (start in cells) != (end in cells):
            if item.rotation % 2 == 0 and start[0] == end[0]:
                return False
            if item.rotation % 2 == 1 and start[1] == end[1]:
                return False
    return True

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
        raise ValueError(f"Unknown Quintal reward: {category}")
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
    if len(placements) > 2 * len(yard_cells(expansion_level)) + MAX_PLACED_ITEMS + 8 * expansion_level:
        raise ValueError("Há mais itens do que espaço no quintal.")
    owned = {str(item.id): item for item in items}
    seen: set[str] = set()
    solid: set[tuple[int, int]] = set()
    paths: set[tuple[int, int]] = set()
    terrain: set[tuple[int, int]] = set()
    water: set[tuple[int, int]] = set()
    bridges: set[tuple[int, int]] = set()
    water_decor: set[tuple[int, int]] = set()
    land_decor: set[tuple[int, int]] = set()
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
        kind = definition.get("layer", "solid")
        layer = paths if kind == "path" else terrain if kind == "terrain" else solid
        if layer & cells:
            raise ValueError("Esta posição já está ocupada.")
        layer.update(cells)
        if owned[item_id].sku == "water_tile":
            water.update(cells)
        if kind == "bridge":
            bridges.update(cells)
        elif kind == "water_decor":
            water_decor.update(cells)
        elif kind == "solid":
            land_decor.update(cells)
        if kind not in GROUND_LAYERS:
            solid_count += 1
    if (paths | land_decor) & water:
        raise ValueError("Caminhos e objetos de terra não podem ocupar o lago.")
    if not (bridges | water_decor) <= water:
        raise ValueError("Pontes e nenúfares devem ficar inteiramente sobre a água.")
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
