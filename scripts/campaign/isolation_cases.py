"""Remaining SEC-02 variants on synthetic accounts and a frozen candidate."""

from datetime import UTC, datetime, timedelta
import hashlib
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

pytest_plugins = ["security_cases"]
from security_cases import NOW, evidence, uid
from extended_cases import seed
from app.persistence_models import (
    CatPhoto,
    Conversation,
    ConversationMember,
    ConversationMessage,
    Friendship,
    HabitatItem,
    HabitatWallet,
    MatchSuggestion,
    MissingReport,
    RemoteCat,
    Sighting,
)
from app.quintal_models import QuintalTask
from app.routers import product, sync


async def cat_state(db):
    async with db.factory() as session:
        cat = await session.get(RemoteCat, uid("matrix-cat"))
        photos = (
            await session.scalars(
                select(CatPhoto)
                .where(CatPhoto.cat_id == cat.id)
                .order_by(CatPhoto.kind)
            )
        ).all()
        return (
            cat.payload,
            cat.analysis,
            cat.deleted_at,
            cat.photo_key,
            cat.photo_hash,
            [(p.kind, p.storage_key, p.content_hash, p.deleted_at) for p in photos],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["matrix-client", "matrix-cat"])
async def test_SEC02_all_reference_angles_and_identifier_namespaces(db, identifier):
    data = await seed(db)
    before = await cat_state(db)
    files = {
        p.relative_to(db.storage.root).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in db.storage.root.rglob("*")
        if p.is_file()
    }
    base = f"/sync/cats/{uid(identifier)}"
    actions = [
        ("GET", base + "/photo", {}),
        ("GET", base + "/avatar/preview", {}),
        ("GET", base + "/avatar/coat", {}),
        ("GET", f"/vision/cats/{uid(identifier)}/jobs", {}),
        ("POST", f"/vision/cats/{uid(identifier)}/enqueue", {}),
        (
            "PUT",
            base + "/photo",
            {
                "data": {
                    "updated_at": (NOW + timedelta(seconds=1)).isoformat(),
                    "device_id": "foreign",
                },
                "files": {"image": ("safe.png", data, "image/png")},
            },
        ),
    ]
    for kind in ("front", "left", "right", "back"):
        path = base + "/reference-photos/" + kind
        actions += [
            ("GET", path, {}),
            ("DELETE", path, {}),
            ("PUT", path, {"files": {"image": ("safe.png", data, "image/png")}}),
        ]
    observed = []
    for actor in ("A", "C", None):
        for method, path, kwargs in actions:
            result = await db.client.request(
                method, path, headers=db.headers(actor) if actor else {}, **kwargs
            )
            assert result.status_code == (401 if actor is None else 404), (
                method,
                path,
                result.status_code,
            )
            assert result.content != data
            assert await cat_state(db) == before
            observed.append(
                {
                    "actor": actor or "anonymous",
                    "method": method,
                    "path": path,
                    "status": result.status_code,
                }
            )
    assert files == {
        p.relative_to(db.storage.root).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in db.storage.root.rglob("*")
        if p.is_file()
    }
    for kind in ("front", "left", "right", "back"):
        result = await db.client.get(
            f"/sync/cats/{uid('matrix-client')}/reference-photos/{kind}",
            headers=db.headers("B"),
        )
        assert result.status_code == 200 and result.content == data
    evidence(
        "SEC02-identifiers-" + identifier,
        {
            "scenario": "SEC-02",
            "checks": observed,
            "owner_all_four_angles": True,
            "foreign_rows_and_file_bytes_unchanged": True,
        },
    )


@pytest.mark.asyncio
async def test_SEC02_sync_collision_analysis_update_and_tombstone_are_owner_scoped(
    db, monkeypatch
):
    await seed(db)
    monkeypatch.setattr(sync, "sync_catalog_sighting", AsyncMock())
    before = await cat_state(db)
    observed = []
    for actor in ("A", "C"):
        for namespace in ("matrix-client", "matrix-cat"):
            for delete in (False, True):
                stamp = NOW + timedelta(seconds=3 if delete else 2)
                op = {
                    "client_id": str(uid(namespace)),
                    "updated_at": stamp.isoformat(),
                    "device_id": actor,
                    "payload": {
                        "individual_id": "pending",
                        "name": actor,
                        "user_id": str(uid("B")),
                    },
                    "analysis": {"attempt": actor},
                    "deleted_at": stamp.isoformat() if delete else None,
                }
                reply = await db.client.post(
                    "/sync/push",
                    headers=db.headers(actor),
                    json={"operations": [op], "user_id": str(uid("B"))},
                )
                assert reply.status_code == 200, reply.text
                assert await cat_state(db) == before
                async with db.factory() as session:
                    own = await session.scalar(
                        select(RemoteCat).where(
                            RemoteCat.user_id == uid(actor),
                            RemoteCat.client_id == uid(namespace),
                        )
                    )
                    assert (
                        own
                        and own.id != uid("matrix-cat")
                        and (own.deleted_at is not None) == delete
                    )
                observed.append(
                    {
                        "actor": actor,
                        "namespace": namespace,
                        "tombstone": delete,
                        "status": 200,
                    }
                )
    reply = await db.client.post("/sync/push", json={"operations": [op]})
    assert reply.status_code == 401 and await cat_state(db) == before
    stamp = (NOW + timedelta(seconds=10)).isoformat()
    op.update(
        client_id=str(uid("matrix-client")),
        device_id="B",
        updated_at=stamp,
        deleted_at=stamp,
    )
    assert (
        await db.client.post(
            "/sync/push", headers=db.headers("B"), json={"operations": [op]}
        )
    ).status_code == 200
    assert (await cat_state(db))[2] is not None
    evidence(
        "SEC02-sync-namespaces",
        {
            "scenario": "SEC-02",
            "checks": observed,
            "foreign_authoritative_analysis_and_media_unchanged": True,
            "owner_delete_positive": True,
            "scope": "Client IDs are per account: allowed writes create/change only the caller record, never B. Sighting reconciliation stubbed; no inference.",
        },
    )


@pytest.mark.asyncio
async def test_SEC02_chat_reads_writes_receipts_and_cross_conversation_ids(db):
    chat, own_chat, message = uid("private-BC"), uid("own-AC"), uid("private-B-message")
    async with db.factory() as session:
        session.add_all([Conversation(id=chat), Conversation(id=own_chat)])
        await session.flush()
        for conv, members in [(chat, ("B", "C")), (own_chat, ("A", "C"))]:
            session.add_all(
                ConversationMember(conversation_id=conv, user_id=uid(actor))
                for actor in members
            )
            session.add_all(
                Friendship(user_id=uid(a), friend_id=uid(b))
                for a, b in (members, members[::-1])
            )
        session.add(
            ConversationMessage(
                id=message,
                conversation_id=chat,
                sender_id=uid("B"),
                body="PRIVATE_B_MESSAGE",
            )
        )
        await session.commit()
    base = f"/social/conversations/{chat}"
    actions = [
        ("GET", base, {}),
        ("GET", base + "/messages", {}),
        ("GET", f"/app/conversations/{chat}", {}),
        ("POST", base + "/messages", {"json": {"body": "attempt"}}),
        ("POST", base + "/read", {}),
        (
            "POST",
            base + f"/messages/{message}/reactions",
            {"json": {"reaction": "paw"}},
        ),
        ("PUT", base + f"/messages/{message}", {"json": {"body": "attempt"}}),
        ("DELETE", base + f"/messages/{message}", {}),
    ]
    statuses = []
    for actor in ("A", None):
        for method, path, kwargs in actions:
            result = await db.client.request(
                method, path, headers=db.headers(actor) if actor else {}, **kwargs
            )
            assert result.status_code in ({403, 404} if actor else {401}), (
                method,
                path,
                result.status_code,
            )
            assert "PRIVATE_B_MESSAGE" not in result.text
            async with db.factory() as session:
                row = await session.get(ConversationMessage, message)
                assert (
                    row.body == "PRIVATE_B_MESSAGE"
                    and row.read_at is None
                    and not row.is_deleted
                    and not row.reactions
                )
            statuses.append(
                {
                    "actor": actor or "anonymous",
                    "method": method,
                    "status": result.status_code,
                }
            )
    for actor, conversation, expected in [("C", chat, 403), ("A", own_chat, 404)]:
        for method, kwargs in [("PUT", {"json": {"body": "attempt"}}), ("DELETE", {})]:
            reply = await db.client.request(
                method,
                f"/social/conversations/{conversation}/messages/{message}",
                headers=db.headers(actor),
                **kwargs,
            )
            assert reply.status_code == expected
    for actor in ("B", "C"):
        reply = await db.client.get(base + "/messages", headers=db.headers(actor))
        assert (
            reply.status_code == 200 and reply.json()[0]["body"] == "PRIVATE_B_MESSAGE"
        )
    path = base + f"/messages/{message}"
    assert (
        await db.client.put(path, headers=db.headers("B"), json={"body": "owner edit"})
    ).status_code == 200
    assert (await db.client.delete(path, headers=db.headers("B"))).status_code == 200
    async with db.factory() as session:
        assert (await session.get(ConversationMessage, message)).is_deleted
    evidence(
        "SEC02-chat-actions",
        {
            "scenario": "SEC-02",
            "checks": statuses,
            "member_cannot_edit_other_sender": True,
            "message_id_cannot_cross_conversation": True,
            "owner_read_edit_delete_positive": True,
        },
    )


@pytest.mark.asyncio
async def test_SEC02_favorites_read_replace_clear_ignore_other_owner_claims(db):
    for actor in ("A", "B", "C"):
        reply = await db.client.put(
            "/app/favorites",
            headers=db.headers(actor),
            json={"favorites": [actor + "-PRIVATE"], "user_id": str(uid("B"))},
        )
        assert reply.status_code == 200
    for actor in ("A", "C"):
        reply = await db.client.get(
            "/app/favorites",
            headers=db.headers(actor),
            params={"user_id": str(uid("B"))},
        )
        assert reply.status_code == 200 and reply.json()["favorites"] == [
            actor + "-PRIVATE"
        ]
        assert (
            await db.client.put(
                "/app/favorites",
                headers=db.headers(actor),
                json={"favorites": [], "user_id": str(uid("B"))},
            )
        ).status_code == 200
        assert (await db.client.get("/app/favorites", headers=db.headers("B"))).json()[
            "favorites"
        ] == ["B-PRIVATE"]
    for method, kwargs in [("GET", {}), ("PUT", {"json": {"favorites": []}})]:
        assert (
            await db.client.request(method, "/app/favorites", **kwargs)
        ).status_code == 401
    evidence(
        "SEC02-favorites-actions",
        {
            "scenario": "SEC-02",
            "read_replace_clear": True,
            "body_and_query_owner_override_ignored": True,
            "foreign_list_unchanged": True,
            "anonymous_denied": True,
        },
    )


@pytest.mark.asyncio
async def test_SEC02_quintal_task_item_cat_and_wallet_scopes(db):
    await seed(db)
    order = await db.client.post(
        "/app/quintal/operations",
        headers=db.headers("B"),
        json={
            "action": "order",
            "target": "chicken",
            "data": {"payment": "coins"},
            "idempotency_key": str(uid("B-order")),
        },
    )
    assert order.status_code == 200, order.text
    purchase = await db.client.post(
        "/app/habitat/purchases",
        headers=db.headers("B"),
        json={"sku": "wood_path", "idempotency_key": str(uid("B-item"))},
    )
    assert purchase.status_code == 200, purchase.text
    item_id = purchase.json()["item"]["id"]
    async with db.factory() as session:
        task = await session.scalar(
            select(QuintalTask).where(QuintalTask.user_id == uid("B"))
        )
        task_id = str(task.id)
        task.status = "ready"
        task.started_at = NOW - timedelta(minutes=2)
        task.ready_at = NOW
        future = datetime.now(UTC) + timedelta(hours=1)
        queued = QuintalTask(
            id=uid("B-queued-preparation"),
            user_id=uid("B"),
            kind="cook",
            target="apple",
            status="active",
            started_at=future,
            ready_at=future + timedelta(minutes=2),
            inputs={"apple": 2, "pear": 2},
            outputs={"ration:apple": 3},
        )
        session.add(queued)
        await session.commit()
        cancel_id = str(queued.id)
        balance = (await session.get(HabitatWallet, uid("B"))).balance
        task_states = {
            str(row.id): {c.name: getattr(row, c.name) for c in row.__table__.columns}
            for row in (
                await session.scalars(
                    select(QuintalTask).where(QuintalTask.user_id == uid("B"))
                )
            ).all()
        }
    observations = []
    for actor in ("A", "C"):
        own = await db.client.get(
            "/app/quintal", headers=db.headers(actor), params={"user_id": str(uid("B"))}
        )
        assert (
            own.status_code == 200
            and task_id not in own.text
            and item_id not in own.text
        )
        for action in ("collect", "cancel"):
            response = await db.client.post(
                "/app/quintal/operations",
                headers=db.headers(actor),
                json={
                    "action": action,
                    "target": task_id if action == "collect" else cancel_id,
                    "data": {},
                    "idempotency_key": str(uid(actor + action)),
                    "user_id": str(uid("B")),
                },
            )
            assert response.status_code == 409, response.text
            observations.append(
                {"actor": actor, "action": action, "status": response.status_code}
            )
        async with db.factory() as session:
            revision = (await session.get(HabitatWallet, uid(actor))).layout_revision
        for placements, cats in [
            ([{"item_id": item_id, "x": 2, "y": 0, "rotation": 0}], []),
            ([], [str(uid("matrix-client"))]),
        ]:
            reply = await db.client.put(
                "/app/habitat/layout",
                headers=db.headers(actor),
                json={
                    "expected_revision": revision,
                    "placements": placements,
                    "selected_cat_ids": cats,
                },
            )
            assert reply.status_code == 422, reply.text
        async with db.factory() as session:
            assert (await session.get(HabitatWallet, uid("B"))).balance == balance
            stored = (
                await session.scalars(
                    select(QuintalTask).where(QuintalTask.user_id == uid("B"))
                )
            ).all()
            assert {
                str(row.id): {
                    c.name: getattr(row, c.name) for c in row.__table__.columns
                }
                for row in stored
            } == task_states
            item = await session.get(HabitatItem, item_id)
            assert item.user_id == uid("B") and item.x is None and item.y is None
    assert (await db.client.get("/app/quintal")).status_code == 401
    assert (
        await db.client.post(
            "/app/quintal/operations",
            json={
                "action": "cancel",
                "target": task_id,
                "data": {},
                "idempotency_key": str(uid("anon-order")),
            },
        )
    ).status_code == 401
    for action, target in [("collect", task_id), ("cancel", cancel_id)]:
        result = await db.client.post(
            "/app/quintal/operations",
            headers=db.headers("B"),
            json={
                "action": action,
                "target": target,
                "data": {},
                "idempotency_key": str(uid("B-positive-" + action)),
            },
        )
        assert result.status_code == 200, result.text
    evidence(
        "SEC02-quintal-actions",
        {
            "scenario": "SEC-02",
            "checks": observations,
            "foreign_item_and_owned_cat_selection_denied_with_current_revision": True,
            "B_wallet_task_and_item_unchanged": True,
            "owner_purchase_order_collect_and_queued_cook_cancel_positive": True,
            "task_fixture": "Persisted ready purchase and future queued preparation; no timer wait or business-rule change.",
        },
    )


@pytest.mark.asyncio
async def test_SEC02_private_clue_and_report_mutations_require_report_owner(
    db, monkeypatch
):
    data = await seed(db)
    monkeypatch.setattr(product, "create_report_matches", AsyncMock())
    key, digest = db.storage.save(
        user_id=uid("A"), cat_id=uid("extra-sighting-cat"), data=data, suffix=".png"
    )
    report_id, match_id = uid("isolation-report"), uid("isolation-match")
    async with db.factory() as session:
        cat = RemoteCat(
            id=uid("extra-sighting-cat"),
            client_id=uid("extra-sighting-client"),
            user_id=uid("A"),
            payload={},
            client_updated_at=NOW,
            updated_by_device="a",
            photo_key=key,
            photo_hash=digest,
            photo_content_type="image/png",
        )
        session.add(cat)
        await session.flush()
        session.add_all(
            [
                MissingReport(
                    id=report_id,
                    user_id=uid("B"),
                    cat_id=uid("matrix-cat"),
                    latitude=-23,
                    longitude=-46,
                    radius_meters=500,
                    status="active",
                ),
                Sighting(
                    id=uid("extra-sighting"),
                    observer_id=uid("A"),
                    cat_id=cat.id,
                    latitude=-23.1,
                    longitude=-46.1,
                ),
            ]
        )
        await session.flush()
        session.add(
            MatchSuggestion(
                id=match_id,
                missing_report_id=report_id,
                sighting_id=uid("extra-sighting"),
                similarity=0.9,
                status="pending",
            )
        )
        await session.commit()
    area = {"latitude": -22, "longitude": -45, "radius_meters": 700}
    actions = [
        ("GET", f"/app/reports/{report_id}/clues", {}),
        ("GET", f"/app/clues/{match_id}/photo", {}),
        ("PUT", f"/app/reports/{report_id}", {"json": area}),
        ("PUT", f"/vision/missing-reports/{report_id}/poster", {"json": {}}),
        ("POST", f"/vision/missing-reports/{report_id}/resolve", {}),
    ]
    actions += [
        ("POST", f"/vision/matches/{match_id}/{decision}", {})
        for decision in ("dismiss", "reopen", "confirm")
    ]
    for actor in ("A", "C", None):
        for method, path, kwargs in actions:
            response = await db.client.request(
                method, path, headers=db.headers(actor) if actor else {}, **kwargs
            )
            assert response.status_code == (401 if actor is None else 404), (
                path,
                response.status_code,
            )
            async with db.factory() as session:
                report = await session.get(MissingReport, report_id)
                match = await session.get(MatchSuggestion, match_id)
                assert (
                    report.latitude,
                    report.longitude,
                    report.radius_meters,
                    report.status,
                ) == (-23, -46, 500, "active")
                assert match.status == "pending" and match.location_revealed_at is None
    assert (
        await db.client.get(f"/app/clues/{match_id}/photo", headers=db.headers("B"))
    ).status_code == 200
    assert (
        await db.client.put(
            f"/vision/missing-reports/{report_id}/poster",
            headers=db.headers("B"),
            json={},
        )
    ).status_code == 200
    for decision in ("dismiss", "reopen", "confirm"):
        response = await db.client.post(
            f"/vision/matches/{match_id}/{decision}", headers=db.headers("B")
        )
        assert response.status_code == 200, response.text
    assert (
        await db.client.put(
            f"/app/reports/{report_id}", headers=db.headers("B"), json=area
        )
    ).status_code == 200
    assert (
        await db.client.post(
            f"/vision/missing-reports/{report_id}/resolve", headers=db.headers("B")
        )
    ).status_code == 204
    evidence(
        "SEC02-clue-report-actions",
        {
            "scenario": "SEC-02",
            "actors": ["owner", "observer", "stranger", "anonymous"],
            "read_area_poster_resolve_and_decisions": True,
            "unauthorized_state_unchanged": True,
            "owner_controls_passed": True,
            "scope": "Synthetic suggestion: permission test, not AI identification. Matching computation stubbed; explicit owner decision only.",
        },
    )
