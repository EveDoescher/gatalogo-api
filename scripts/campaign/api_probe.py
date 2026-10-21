"""External A/B probe: real disposable DB + ASGI, no lifespan/AI/delivery.

The target source directory is frozen; monkeypatches exist only in this process.
Results never contain tokens, DB URLs, SQL parameters or real user data.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import socket
import sys
import time
import traceback
from unittest.mock import AsyncMock

from fixtures import NOW, catalog_rows, uid
from measurements import resources, save, summarize


def prohibit_external_network():
    original = socket.socket.connect

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("Campaign forbids external network")
        return original(sock, address)

    socket.socket.connect = connect


async def probe(args):
    source = Path(args.source).resolve()
    run = Path(os.environ["QUALITY_CAMPAIGN_RUN"]).resolve()
    if not source.is_relative_to(run / "sources"):
        raise ValueError("Only frozen campaign copies may be imported")
    target = Path(args.output).resolve()
    if not target.is_relative_to(run):
        raise ValueError("Evidence must stay inside campaign directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(source))
    os.environ.update(SMTP_ENABLED="false", GEMINI_API_KEY="", FCM_SERVICE_ACCOUNT_FILE="",
                      PHOTO_STORAGE_DIR=str(target.parent / "storage"),
                      ANALYSIS_CACHE_DIR=str(target.parent / "cache"))
    prohibit_external_network()
    from sqlalchemy import event, func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from httpx import ASGITransport, AsyncClient
    from app import main
    from app.database import Base, get_database_session
    from app.persistence_models import (
        CatPhoto, HabitatTransaction, HabitatWallet, RemoteCat, SyncChange, User, UserSession,
    )
    from app.quintal_models import QuintalFacility, QuintalTask
    from app.services.auth_service import issue_tokens
    from app.services.quintal_production import operation
    from app.services import progression
    from app.config import get_settings
    from app.exceptions import MissingApiKeyError

    url = os.environ.get("RECOGNITION_TEST_DATABASE_URL", "")
    if not url.endswith("/recognition_test") or ":5432/" in url:
        raise ValueError("A random-port disposable recognition_test database is required")
    schema = "pilot_" + args.label.replace("-", "_").lower()
    if not schema.replace("_", "").isalnum():
        raise ValueError("Invalid schema")
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema + ",public"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report = {"label": args.label, "transport": "in-process ASGI, real PostgreSQL; no TCP latency",
              "source": str(source), "started_at": datetime.now(UTC).isoformat(),
              "cases": [], "samples": [], "resources_start": resources(),
              "external_services": "analysis and FCM substituted; no lifespan/workers",
              "fixture": {"seed": 2102026, "cats_A": 40, "cats_B": 1, "clock": NOW.isoformat()}}
    sql = []
    checked_out = 0
    inflight_sql = {}

    def before(connection, cursor, statement, params, context, many):
        inflight_sql[id(context)] = time.perf_counter()

    def after(connection, cursor, statement, params, context, many):
        sql.append({"kind": statement.lstrip().split()[0].upper(),
                    "elapsed_ms": (time.perf_counter() - inflight_sql.pop(id(context), time.perf_counter())) * 1000})

    def checkout(*unused):
        nonlocal checked_out
        checked_out += 1

    def checkin(*unused):
        nonlocal checked_out
        checked_out -= 1

    for name, handler in (("before_cursor_execute", before), ("after_cursor_execute", after),
                          ("checkout", checkout), ("checkin", checkin)):
        event.listen(engine.sync_engine, name, handler)

    def case(scenario, variant, condition, observed):
        report["cases"].append({"scenario": scenario, "variant": variant,
                                "status": "PASS" if condition else "FAIL", "observed": observed})

    try:
        async with admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            assert await db.scalar(text("SELECT current_schema()")) == schema, "Refusing DDL outside owned schema"
            await db.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
        settings = get_settings()
        settings.habitat_free_shop = False
        from app.services.fcm_service import fcm_sender
        fcm_sender.send_notification = AsyncMock(return_value={"delivered": [], "retry": False})
        tokens = {}
        async with factory() as db:
            for who in ("A", "B"):
                user = User(id=uid(who), email=f"{who.lower()}@campaign.invalid", status="active")
                db.add(user)
                await db.flush()
                tokens[who] = (await issue_tokens(db, user=user, device_id="a1"))["access_token"]
                db.add(HabitatWallet(user_id=user.id, balance=100, care_state={},
                                     selected_cat_ids=[], expansion_level=0))
                db.add(QuintalFacility(user_id=user.id, kind="market", level=1, target_level=0))
            await db.flush()
            for values in catalog_rows(40):
                values.pop("revisions")
                cat = RemoteCat(**values)
                db.add(cat)
                await db.flush()
                db.add(SyncChange(user_id=cat.user_id, cat_id=cat.id))
                for kind in ("primary", "front", "left", "back"):
                    db.add(CatPhoto(id=uid(f"{cat.id}/{kind}"), cat_id=cat.id, kind=kind,
                                    storage_key=f"synthetic/{kind}.png", content_hash="a" * 64,
                                    status="ready", updated_at=NOW,
                                    deleted_at=NOW if kind == "back" else None))
            other = RemoteCat(id=uid("foreign-cat"), user_id=uid("B"), client_id=uid("foreign-client"),
                              client_updated_at=NOW, updated_by_device="b1", payload={"private": "B-only"})
            db.add(other)
            await db.flush()
            db.add(SyncChange(user_id=other.user_id, cat_id=other.id))
            await db.commit()

        async def database():
            async with factory() as db:
                try:
                    yield db
                finally:
                    await db.rollback()

        main.app.dependency_overrides[get_database_session] = database
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://campaign") as client:
            async def request(method, path, *, who="A", label="", **kwargs):
                start_sql = len(sql)
                start_cpu = time.process_time()
                start = time.perf_counter()
                headers = {"Authorization": "Bearer " + tokens[who]} if who else {}
                response = await client.request(method, path, headers=headers, **kwargs)
                sample = {"label": label, "elapsed_ms": (time.perf_counter() - start) * 1000,
                          "cpu_ms": (time.process_time() - start_cpu) * 1000,
                          "status": response.status_code, "response_bytes": len(response.content),
                          "sql": sql[start_sql:], "auth_included": True}
                report["samples"].append(sample)
                return response, sample

            for variant, token in (("missing", None), ("invalid-signature", "invalid")):
                old = tokens["A"]
                if token:
                    tokens["A"] = token
                response, _ = await request("GET", "/sync/pull", who="A" if token else None, label="SEC-01")
                tokens["A"] = old
                case("SEC-01", variant, response.status_code == 401, {"status": response.status_code})

            import jwt
            expired = jwt.encode({"sub": str(uid("A")), "sid": str(uid("expired")), "type": "access",
                                  "iss": settings.jwt_issuer, "exp": datetime.now(UTC) - timedelta(seconds=1)},
                                 settings.jwt_secret, algorithm="HS256")
            old = tokens["A"]
            tokens["A"] = expired
            response, _ = await request("GET", "/sync/pull", label="SEC-01")
            tokens["A"] = old
            case("SEC-01", "expired", response.status_code == 401, {"status": response.status_code})

            response, sample = await request("GET", "/sync/pull?limit=40", label="DB-02")
            body = response.json()
            expected_ids = {str(uid(f"client-{i}")) for i in range(40)}
            actual_ids = {row["client_id"] for row in body.get("changes", [])}
            case("SEC-02", "catalog-account-boundary", response.status_code == 200 and actual_ids == expected_ids,
                 {"status": response.status_code, "visible_A": len(actual_ids), "foreign_visible": str(uid("foreign-client")) in actual_ids})
            other_reply, _ = await request("GET", "/sync/pull?limit=40", who="B", label="SEC-02")
            other_ids = {r["client_id"] for r in other_reply.json().get("changes", [])}
            case("SEC-02", "reverse-catalog-boundary", other_reply.status_code == 200 and
                 other_ids == {str(uid("foreign-client"))}, {"visible_B": len(other_ids)})
            case("DB-02", "snapshot-contract", actual_ids == expected_ids and all(
                {p["kind"] for p in row["reference_photos"]} == {"front", "left"}
                and row["payload"]["unknown"] == [1, 2] for row in body.get("changes", [])),
                 {"selects_including_auth": sum(s["kind"] == "SELECT" for s in sample["sql"])})
            save(target.parent / "pull_response.json", body)
            foreign, _ = await request("GET", f'/sync/cats/{uid("foreign-client")}/photo', label="SEC-02")
            case("SEC-02", "private-photo", foreign.status_code == 404, {"status": foreign.status_code})

            # Warm-up and sequential timing are a bounded pilot, not a load benchmark.
            for index in range(5 + 30):
                reply, row = await request("GET", "/sync/pull?limit=40", label="LAT-04")
                row["warmup"] = index < 5
                assert reply.status_code == 200
                assert reply.json() == body
            case("LAT-04", "repeated-read-integrity", True, {"warmup": 5, "measured": 30})

            held = []
            class Analyzer:
                def __init__(self, settings):
                    pass

                async def analyze(self, **kwargs):
                    held.append(checked_out)
                    await asyncio.sleep(.002)
                    raise MissingApiKeyError("controlled unavailable")

            main.AnalysisService = Analyzer
            error_reply, _ = await request("POST", "/analyze", label="LAT-03",
                                           data={"cat_id": str(uid("client-0"))},
                                           files={"image": ("synthetic.png", b"synthetic", "image/png")})
            case("LAT-03", "controlled-503", error_reply.status_code == 503 and
                 error_reply.json().get("detail") == {"code": "MISSING_API_KEY", "message": "controlled unavailable"},
                 {"status": error_reply.status_code, "detail": error_reply.json().get("detail")})
            # Diagnostic comparison: A may retain a connection. Do not call the historical behavior a B regression.
            case("DB-05", "primary-inference", held == [0], {"connections_during_substituted_IA": held})

        async def transact(who, key, data=None):
            async with factory() as db:
                await progression.account(db, uid(who))
                result = await operation(db, uid(who), "order", "chicken", data or {"payment": "coins"}, key, now=NOW)
                await db.commit()
                return result

        key = uid("order-intent")
        start = time.perf_counter()
        results = await asyncio.gather(transact("A", key), transact("A", key))
        await engine.dispose()  # simulate reconnection after a committed response is lost
        replay = await transact("A", key)
        async with factory() as db:
            balance = (await db.get(HabitatWallet, uid("A"))).balance
            tasks = await db.scalar(select(func.count()).select_from(QuintalTask).where(QuintalTask.user_id == uid("A")))
            receipts = await db.scalar(select(func.count()).select_from(HabitatTransaction).where(
                HabitatTransaction.user_id == uid("A"), HabitatTransaction.event_key == f"quintal:{key}"))
        case("DATA-03", "parallel-and-reconnect-replay", results[0] == results[1] == replay and balance == 88 and tasks == receipts == 1,
             {"balance": balance, "tasks": tasks, "receipts": receipts, "elapsed_ms": (time.perf_counter() - start) * 1000})
        rejected = False
        try:
            await transact("A", key, {"payment": "products"})
        except ValueError:
            rejected = True
        await transact("B", key)
        async with factory() as db:
            balances = [(await db.get(HabitatWallet, uid(who))).balance for who in ("A", "B")]
        case("DATA-04", "fingerprint-and-account", rejected and balances == [88, 88],
             {"fingerprint_rejected": rejected, "balances": balances})
        # Revoke actual session and verify authorization again, retaining no token in evidence.
        async with factory() as db:
            sessions = (await db.scalars(select(UserSession).where(UserSession.user_id == uid("A")))).all()
            for item in sessions:
                item.revoked_at = datetime.now(UTC)
            await db.commit()
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://campaign") as client:
            response = await client.get("/sync/pull", headers={"Authorization": "Bearer " + tokens["A"]})
            case("SEC-01", "revoked", response.status_code == 401, {"status": response.status_code})
    except Exception:
        report["infrastructure_error"] = traceback.format_exc()
    finally:
        report["resources_end"] = resources()
        report["lat04"] = summarize([s for s in report["samples"] if s["label"] == "LAT-04" and not s.get("warmup")])
        report["finished_at"] = datetime.now(UTC).isoformat()
        save(target, report)
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()
    return 2 if report.get("infrastructure_error") else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--label", required=True)
    raise SystemExit(asyncio.run(probe(parser.parse_args())))
