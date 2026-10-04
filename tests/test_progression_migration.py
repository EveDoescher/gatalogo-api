from datetime import UTC, datetime
from uuid import uuid4
import importlib.util
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy import create_engine, select
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.persistence_models import User, ProductState, RemoteCat, CatPhoto, HabitatWallet


def test_migration_preserves_balances_and_reserves_legacy_photo_hashes():
    engine = create_engine("sqlite://")
    user, cat, client = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        for model in (User, ProductState, RemoteCat, CatPhoto, HabitatWallet):
            model.__table__.create(connection)
        connection.execute(User.__table__.insert().values(id=user, email="migration@test.local", status="active"))
        connection.execute(ProductState.__table__.insert().values(user_id=user,
            preferences={"public_gallery": True, "gamification": {"xp": 3200, "highest_level": 7}},
            achievements={"night_owl": "2026-09-01"}))
        connection.execute(HabitatWallet.__table__.insert().values(user_id=user, balance=87))
        connection.execute(RemoteCat.__table__.insert().values(id=cat, user_id=user, client_id=client,
            payload={}, photo_hash="primary", client_updated_at=datetime.now(UTC), updated_by_device="old"))
        connection.execute(CatPhoto.__table__.insert().values(cat_id=cat, kind="front", storage_key="old",
            content_hash="reference", content_type="image/jpeg", status="ready"))
        path = Path(__file__).parents[1] / "alembic/versions/20260927_11_progression.py"
        spec = importlib.util.spec_from_file_location("progress_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        prefs = connection.execute(select(ProductState.preferences)).scalar_one()
        assert prefs["gamification"]["xp"] == 3200
        assert prefs["gallery_audience"] == prefs["medals_audience"] == prefs["habitat_audience"] == "friends"
        assert connection.execute(select(HabitatWallet.balance)).scalar_one() == 87
        ledger = sa.Table("progress_events", sa.MetaData(), autoload_with=connection)
        entries = connection.execute(select(ledger)).mappings().all()
        assert {row["photo_hash"] for row in entries} == {"primary", "reference"}
        assert all(row["xp"] == row["coins"] == 0 for row in entries)
    engine.dispose()
