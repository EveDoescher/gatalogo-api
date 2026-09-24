"""Habitat wallet, purchase and layout contract tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.persistence_models import HabitatItem, HabitatTransaction, HabitatWallet, RemoteCat, User
from app.routers.habitat import ExpansionRequest, LayoutRequest, PlacementRequest, PurchaseRequest, expand_yard, get_habitat, purchase, save_layout
from app.services.habitat_service import grant_reward, item_cells, normalize_legacy_layout, validate_placements, yard_cells


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
async def test_old_catalog_and_guest_sync_are_backfilled_once(account):
    session, user = account
    cat = RemoteCat(user_id=user.id, client_id=uuid4(), payload={"is_owned": True, "name": "Lua"},
                    analysis={"primary_color": "Branco"}, photo_key="private/photo", photo_hash="old-photo",
                    client_updated_at=datetime.now(UTC), updated_by_device="test")
    session.add(cat)
    await session.commit()
    first = await get_habitat(user=user, session=session)
    second = await get_habitat(user=user, session=session)
    assert first["balance"] == second["balance"] == 25
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
        assert first == retry
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
