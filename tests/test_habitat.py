"""Habitat wallet, purchase and layout contract tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.persistence_models import HabitatItem, HabitatTransaction, HabitatWallet, RemoteCat, User
from app.routers.habitat import ExpansionRequest, LayoutRequest, PlacementRequest, PurchaseRequest, expand_yard, get_habitat, purchase, save_layout
from app.services.habitat_service import SHOP, REWARDS, can_cross_bridge, grant_reward, item_cells, normalize_legacy_layout, validate_placements, walkable_cells, yard_cells
from app.services.habitat_interactions import care_summary, interact


@pytest.mark.asyncio
async def test_cottage_costs_five_catalogs_and_retries_never_charge_twice(account):
    session, user = account
    for index in range(5):
        await grant_reward(session, user.id, "catalog", f"catalog:{index}", photo_hash=f"photo:{index}")
    await session.commit()
    request = PurchaseRequest(sku="green_cottage", idempotency_key=uuid4())
    first = await purchase(request, user=user, session=session)
    retry = await purchase(request, user=user, session=session)
    assert first == retry
    assert first["balance"] == 5
    assert SHOP["large_cottage"]["price"] == 6 * REWARDS["catalog"]
    ledger = (await session.execute(select(HabitatTransaction).where(HabitatTransaction.category == "purchase"))).scalars().all()
    assert len(ledger) == 1 and ledger[0].amount == -120


@pytest.mark.asyncio
async def test_new_decorations_keep_ownership_limits_and_rotated_collision(account):
    session, user = account
    await get_habitat(user=user, session=session)
    wallet = await session.get(HabitatWallet, user.id)
    wallet.balance = 200
    await session.commit()
    for _ in range(SHOP["garden_planter"]["max_owned"]):
        await purchase(PurchaseRequest(sku="garden_planter", idempotency_key=uuid4()), user=user, session=session)
    with pytest.raises(HTTPException):
        await purchase(PurchaseRequest(sku="garden_planter", idempotency_key=uuid4()), user=user, session=session)
    for rotation in range(4):
        assert len(item_cells("solar_cottage", 1, 1, rotation)) == 9
        assert len(item_cells("wood_platform", 1, 1, rotation)) == 4


@pytest_asyncio.fixture
async def account():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (User.__table__, RemoteCat.__table__, HabitatWallet.__table__, HabitatTransaction.__table__, HabitatItem.__table__):
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email=f"{uuid4()}@test.local", status="active")
        session.add(user)
        await session.commit()
        yield session, user
    await engine.dispose()


async def _place_pet_and_bowl(session, user):
    await get_habitat(user=user, session=session)
    cat = RemoteCat(user_id=user.id, client_id=uuid4(), payload={"is_owned": True, "name": "Lua"},
                    client_updated_at=datetime.now(UTC), updated_by_device="test")
    bowl = HabitatItem(id=uuid4(), user_id=user.id, sku="food_bowl", x=4, y=3, rotation=0)
    session.add_all([cat, bowl])
    wallet = await session.get(HabitatWallet, user.id)
    wallet.selected_cat_ids = [str(cat.client_id)]
    wallet.cat_anchors = {str(cat.client_id): [3, 3]}
    await session.commit()
    return cat, bowl, wallet


@pytest.mark.asyncio
async def test_feeding_has_daily_allowance_and_idempotent_consumption(account):
    session, user = account
    cat, bowl, wallet = await _place_pet_and_bowl(session, user)
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    key = uuid4()
    for _ in range(2):
        await interact(session, user.id, "feed", cat.client_id, key, bowl.id, now=now)
    assert care_summary(wallet.care_state, 3, now)["free_portions"] == 2
    assert wallet.care_state["cats"][str(cat.client_id)]["affinity"] == 5
    with pytest.raises(ValueError, match="hoje"):
        await interact(session, user.id, "feed", cat.client_id, uuid4(), bowl.id, now=now)
    with pytest.raises(ValueError, match="outro destino"):
        await interact(session, user.id, "treat", cat.client_id, key, bowl.id, now=now)
    tomorrow = now + timedelta(days=1)
    assert care_summary(wallet.care_state, 3, tomorrow)["free_portions"] == 3
    await interact(session, user.id, "feed", cat.client_id, uuid4(), bowl.id, now=tomorrow)
    assert wallet.care_state["cats"][str(cat.client_id)]["affinity"] == 10
    assert wallet.balance == 0


@pytest.mark.asyncio
async def test_treats_charge_once_and_reject_insufficient_balance_or_foreign_cats(account):
    session, user = account
    cat, bowl, wallet = await _place_pet_and_bowl(session, user)
    key = uuid4()
    with pytest.raises(ValueError, match="insuficientes"):
        await interact(session, user.id, "treat", cat.client_id, key, bowl.id)
    wallet.balance = 4
    await interact(session, user.id, "treat", cat.client_id, key, bowl.id)
    await interact(session, user.id, "treat", cat.client_id, key, bowl.id)
    assert wallet.balance == 0
    assert wallet.care_state["cats"][str(cat.client_id)]["affinity"] == 3
    with pytest.raises(ValueError, match="seus gatos"):
        await interact(session, user.id, "pet", uuid4(), uuid4())
    assert len((await session.execute(select(HabitatTransaction).where(HabitatTransaction.category == "treat"))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_feeding_requires_an_accessible_owned_bowl(account):
    session, user = account
    cat, bowl, _ = await _place_pet_and_bowl(session, user)
    with pytest.raises(ValueError, match="potinho"):
        await interact(session, user.id, "feed", cat.client_id, uuid4(), uuid4())
    for x, y in ((3, 3), (5, 3), (4, 2), (4, 4)):
        session.add(HabitatItem(user_id=user.id, sku="small_rock", x=x, y=y, rotation=0))
    await session.flush()
    with pytest.raises(ValueError, match="caminho livre"):
        await interact(session, user.id, "feed", cat.client_id, uuid4(), bowl.id)


@pytest.mark.asyncio
async def test_affinity_gift_is_once_and_absence_never_reduces_affinity(account):
    session, user = account
    cat, _, wallet = await _place_pet_and_bowl(session, user)
    now = datetime(2026, 9, 24, tzinfo=UTC)
    wallet.care_state = {"cats": {str(cat.client_id): {"affinity": 19}}}
    await interact(session, user.id, "pet", cat.client_id, uuid4(), now=now)
    await interact(session, user.id, "pet", cat.client_id, uuid4(), now=now + timedelta(days=40))
    gifts = (await session.execute(select(HabitatItem).where(HabitatItem.sku == "friendship_garden"))).scalars().all()
    assert len(gifts) == 1
    assert wallet.care_state["cats"][str(cat.client_id)]["affinity"] == 21


@pytest.mark.asyncio
async def test_plants_grow_by_server_clock_and_watering_cannot_be_spammed(account):
    session, user = account
    plant = HabitatItem(id=uuid4(), user_id=user.id, sku="leaf_garden", x=1, y=1, rotation=0)
    session.add(plant)
    await session.commit()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    key = uuid4()
    await interact(session, user.id, "water", plant.id, key, now=now)
    await interact(session, user.id, "water", plant.id, key, now=now)
    assert datetime.fromisoformat(plant.care_state["grow_at"]) == now + timedelta(hours=12)
    with pytest.raises(ValueError, match="seis horas"):
        await interact(session, user.id, "water", plant.id, uuid4(), now=now + timedelta(hours=5))
    await interact(session, user.id, "water", plant.id, uuid4(), now=now + timedelta(hours=6))
    assert datetime.fromisoformat(plant.care_state["grow_at"]) == now + timedelta(hours=6)
    with pytest.raises(ValueError, match="já cresceu"):
        await interact(session, user.id, "water", plant.id, uuid4(), now=now + timedelta(hours=7))


def test_water_layers_block_walks_and_bridges_obey_their_rotated_entrances():
    owner = uuid4()
    water = [HabitatItem(id=uuid4(), user_id=owner, sku="water_tile", x=x, y=y, rotation=0)
             for x in (2, 3) for y in (2, 3)]
    bridge = HabitatItem(id=uuid4(), user_id=owner, sku="wood_bridge", x=2, y=2, rotation=0)
    def placements(items):
        return [{"item_id": str(item.id), "x": item.x, "y": item.y, "rotation": item.rotation} for item in items]
    assert (2, 2) not in walkable_cells(water, 0)
    validate_placements(water + [bridge], placements(water + [bridge]))
    assert (2, 2) in walkable_cells(water + [bridge], 0)
    assert can_cross_bridge((1, 2), (2, 2), [bridge])
    assert not can_cross_bridge((2, 1), (2, 2), [bridge])
    bridge.rotation = 1
    assert not can_cross_bridge((1, 2), (2, 2), [bridge])
    assert can_cross_bridge((2, 1), (2, 2), [bridge])
    with pytest.raises(ValueError, match="inteiramente"):
        validate_placements(water + [bridge], placements(water[1:] + [bridge]))
    path = HabitatItem(id=uuid4(), user_id=owner, sku="pebble_path", x=2, y=2, rotation=0)
    with pytest.raises(ValueError, match="lago"):
        validate_placements(water + [path], placements(water + [path]))


@pytest.mark.asyncio
async def test_water_batch_purchase_is_atomic_and_replay_safe(account):
    session, user = account
    await get_habitat(user=user, session=session)
    wallet = await session.get(HabitatWallet, user.id)
    wallet.balance = 25
    await session.commit()
    request = PurchaseRequest(sku="water_tile", quantity=5, idempotency_key=uuid4())
    first = await purchase(request, user=user, session=session)
    retry = await purchase(request, user=user, session=session)
    assert first == retry
    assert first["balance"] == 0 and len(first["items"]) == 5


@pytest.mark.asyncio
async def test_catalog_reward_is_once_per_cat_and_photo(account):
    session, user = account
    assert await grant_reward(session, user.id, "catalog", "catalog:one", photo_hash="same") == 25
    assert await grant_reward(session, user.id, "catalog", "catalog:one", photo_hash="same") == 0
    assert await grant_reward(session, user.id, "catalog", "catalog:two", photo_hash="same") == 0
    assert await grant_reward(session, user.id, "catalog", "catalog:three", photo_hash="different") == 25
    wallet = await session.get(HabitatWallet, user.id)
    assert wallet.balance == 50
    assert len((await session.execute(select(HabitatItem))).scalars().all()) == 4


@pytest.mark.asyncio
async def test_daily_limit_and_retry_do_not_move_to_a_later_reward(account):
    session, user = account
    for index in range(7):
        earned = await grant_reward(session, user.id, "like", f"like:{index}")
        assert earned == (1 if index < 5 else 0)
    assert await grant_reward(session, user.id, "like", "like:6") == 0
    assert (await session.get(HabitatWallet, user.id)).balance == 5


@pytest.mark.asyncio
async def test_purchase_is_atomic_idempotent_and_checks_balance(account):
    session, user = account
    user_id = user.id
    key = uuid4()
    with pytest.raises(HTTPException, match="Moedas insuficientes"):
        await purchase(PurchaseRequest(sku="yellow_flowers", idempotency_key=key), user=user, session=session)
    await session.rollback()
    user = await session.get(User, user_id)
    await grant_reward(session, user_id, "catalog", "catalog:first", photo_hash="first")
    await session.commit()
    first = await purchase(PurchaseRequest(sku="yellow_flowers", idempotency_key=key), user=user, session=session)
    retry = await purchase(PurchaseRequest(sku="yellow_flowers", idempotency_key=key), user=user, session=session)
    assert first == retry
    assert first["balance"] == 5
    purchases = (await session.execute(select(HabitatTransaction).where(HabitatTransaction.category == "purchase"))).scalars().all()
    assert len(purchases) == 1 and purchases[0].amount == -20


@pytest.mark.asyncio
async def test_old_catalog_never_receives_retroactive_rewards(account):
    session, user = account
    cat = RemoteCat(user_id=user.id, client_id=uuid4(), payload={"is_owned": True, "name": "Lua"},
                    analysis={"primary_color": "Branco"}, photo_key="private/photo", photo_hash="old-photo",
                    client_updated_at=datetime.now(UTC), updated_by_device="test")
    session.add(cat)
    await session.commit()
    first = await get_habitat(user=user, session=session)
    second = await get_habitat(user=user, session=session)
    assert first["balance"] == second["balance"] == 0
    assert first["cats"][0]["name"] == "Lua"


@pytest.mark.asyncio
async def test_layout_checks_inventory_collision_cat_ownership_and_revision(account):
    session, user = account
    initial = await get_habitat(user=user, session=session)
    bed, tree = initial["items"][:2]
    with pytest.raises(ValueError, match="ocupada"):
        validate_placements((await session.execute(select(HabitatItem))).scalars().all(), [
            {"item_id": bed["id"], "x": 1, "y": 1, "rotation": 0},
            {"item_id": tree["id"], "x": 1, "y": 1, "rotation": 0},
        ])
    result = await save_layout(LayoutRequest(expected_revision=0, placements=[
        PlacementRequest(item_id=bed["id"], x=1, y=1, rotation=0)
    ], selected_cat_ids=[]), user=user, session=session)
    assert result["revision"] == 1
    with pytest.raises(HTTPException) as conflict:
        await save_layout(LayoutRequest(expected_revision=0, placements=[], selected_cat_ids=[]), user=user, session=session)
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_path_batch_is_atomic_idempotent_and_separate_from_decorations(account):
    session, user = account
    initial = await get_habitat(user=user, session=session)
    wallet = await session.get(HabitatWallet, user.id)
    wallet.balance = 100
    await session.commit()
    key = uuid4()
    request = PurchaseRequest(sku="pebble_path", quantity=5, idempotency_key=key)
    first = await purchase(request, user=user, session=session)
    retry = await purchase(request, user=user, session=session)
    assert first == retry
    assert len(first["items"]) == 5
    assert first["balance"] == 70
    bed = initial["items"][0]
    path = first["items"][0]
    result = await save_layout(LayoutRequest(expected_revision=0, placements=[
        PlacementRequest(item_id=bed["id"], x=1, y=1, rotation=0),
        PlacementRequest(item_id=path["id"], x=1, y=1, rotation=0),
    ], selected_cat_ids=[]), user=user, session=session)
    assert len([item for item in result["items"] if item["x"] is not None]) == 2
    with pytest.raises(HTTPException):
        await purchase(PurchaseRequest(sku="wood_path", quantity=5, idempotency_key=key), user=user, session=session)


@pytest.mark.asyncio
async def test_three_expansions_charge_once_and_unlock_irregular_cells(account):
    session, user = account
    await get_habitat(user=user, session=session)
    wallet = await session.get(HabitatWallet, user.id)
    wallet.balance = 600
    await session.commit()
    assert len(yard_cells(0)) < len(yard_cells(1)) < len(yard_cells(2)) < len(yard_cells(3))
    assert (0, -1) not in yard_cells(0) or (-1, -1) not in yard_cells(0)
    for level in range(3):
        request = ExpansionRequest(expected_level=level, idempotency_key=uuid4())
        first = await expand_yard(request, user=user, session=session)
        retry = await expand_yard(request, user=user, session=session)
        assert {key: value for key, value in first.items() if key != "care"} == {key: value for key, value in retry.items() if key != "care"}
        assert first["care"]["free_portions"] == retry["care"]["free_portions"]
        assert first["expansion_level"] == level + 1
        assert first["cat_capacity"] == level + 4
        assert first["balance"] == 600 - sum((80, 180, 320)[:level + 1])
    with pytest.raises(ValueError):
        await expand_yard(ExpansionRequest(expected_level=3, idempotency_key=uuid4()), user=user, session=session)


@pytest.mark.asyncio
@pytest.mark.parametrize('rules_version', [1, 2])
async def test_bed_axis_migration_repositions_collisions_and_preserves_free_cat_anchors(account, rules_version):
    session, user = account
    await get_habitat(user=user, session=session)
    wallet = await session.get(HabitatWallet, user.id)
    items = (await session.execute(select(HabitatItem).where(HabitatItem.user_id == user.id))).scalars().all()
    bed = next(item for item in items if item.sku == "cat_bed")
    tree = next(item for item in items if item.sku == "small_tree")
    bed.x = bed.y = 1
    tree.x, tree.y = 1, 2
    wallet.layout_rules_version = rules_version
    wallet.selected_cat_ids = ['free-cat', 'blocked-cat']
    wallet.cat_anchors = {'free-cat': [5, 5], 'blocked-cat': [1, 2]}
    assert normalize_legacy_layout(wallet, items)
    validate_placements(items, [
        {"item_id": str(item.id), "x": item.x, "y": item.y, "rotation": item.rotation}
        for item in items if item.x is not None
    ])
    assert (tree.x, tree.y) != (1, 2)
    assert wallet.cat_anchors['free-cat'] == [5, 5]
    assert tuple(wallet.cat_anchors['blocked-cat']) not in item_cells('cat_bed', 1, 1, 0)
    assert wallet.layout_rules_version == 3
    revision = wallet.layout_revision
    assert not normalize_legacy_layout(wallet, items)
    assert wallet.layout_revision == revision


@pytest.mark.parametrize('rotation', range(4))
def test_bed_long_axis_matches_its_sprite_and_rotates_collision_cells(rotation):
    expected = {(3, 3), (4, 3)} if rotation % 2 else {(3, 3), (3, 4)}
    assert item_cells('cat_bed', 3, 3, rotation) == expected


def test_directional_footprints_and_fixed_house_rotation():
    user_id = uuid4()
    log = HabitatItem(id=uuid4(), user_id=user_id, sku="large_log")
    bush = HabitatItem(id=uuid4(), user_id=user_id, sku="small_bush")
    house = HabitatItem(id=uuid4(), user_id=user_id, sku="green_cottage")
    base = {"item_id": str(bush.id), "x": 2, "y": 3, "rotation": 0}
    validate_placements([log, bush], [base, {"item_id": str(log.id), "x": 2, "y": 2, "rotation": 0}])
    with pytest.raises(ValueError, match="ocupada"):
        validate_placements([log, bush], [base, {"item_id": str(log.id), "x": 2, "y": 2, "rotation": 1}])
    with pytest.raises(ValueError, match="girado"):
        validate_placements([house], [{"item_id": str(house.id), "x": 2, "y": 2, "rotation": 1}])
