"""Real FCM sender/PG receipts with isolated HTTP transport, never live FCM."""

from datetime import timedelta
import json

import httpx
import pytest

pytest.register_assert_rewrite("security_cases")
pytest_plugins = ["security_cases"]
from security_cases import evidence, uid, NOW
from app.persistence_models import (
    AppNotification,
    DeviceToken,
    HabitatTransaction,
    ProductState,
)
from app.services import fcm_service
from app.services.quintal_push import deliver_pending

TYPES = [
    ("quintal_ready", "quintal_notifications"),
    ("habitat_gift", "quintal_notifications"),
    ("possible_match", "sighting_notifications"),
    ("match_confirmed", "sighting_notifications"),
    ("message", "social_notifications"),
    ("friend_invite", "social_notifications"),
    ("friend_accepted", "social_notifications"),
    ("achievement", None),
]


def sender(db, monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(fcm_service, "get_session_factory", lambda: db.factory)
    monkeypatch.setattr(
        fcm_service.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    service = fcm_service.FcmSender()
    monkeypatch.setattr(
        service, "_access_token", lambda: ("synthetic", "isolated-project")
    )
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,preference", TYPES)
async def test_DLV04_preferences_partial_success_and_targeted_retry(
    db, monkeypatch, kind, preference
):
    labels = [
        "good",
        "temporary",
        "unregistered",
        "invalid",
        "network",
        "muted",
        "delivered",
        "foreign",
    ]
    async with db.factory() as session:
        for label in labels:
            session.add(
                DeviceToken(
                    id=uid("fcm-" + label),
                    user_id=uid("B" if label == "foreign" else "A"),
                    token="synthetic-" + label,
                    platform="android",
                    preferences={preference: False}
                    if label == "muted" and preference
                    else {},
                )
            )
        await session.commit()
    calls = []
    retry = False

    def transport(request):
        payload = json.loads(request.content)["message"]
        label = payload["token"].removeprefix("synthetic-")
        calls.append(label)
        assert payload["data"]["notification_id"] == "synthetic-notice"
        assert payload["android"]["notification"]["tag"] == "synthetic-notice"
        if not retry:
            if label == "network":
                raise httpx.ConnectError("controlled offline", request=request)
            if label == "unregistered":
                return httpx.Response(
                    404, json={"error": {"details": [{"errorCode": "UNREGISTERED"}]}}
                )
            if label in ("temporary", "invalid"):
                return httpx.Response(
                    503 if label == "temporary" else 400,
                    json={"error": {"details": [{"errorCode": "INVALID_ARGUMENT"}]}},
                )
        return httpx.Response(200, json={"name": "synthetic-acceptance"})

    service = sender(db, monkeypatch, transport)
    previous = [str(uid("fcm-delivered"))]
    first = await service.send_notification(
        user_id=uid("A"),
        notification_id="synthetic-notice",
        notification_type=kind,
        delivered=previous,
    )
    expected = {"good", "temporary", "unregistered", "invalid", "network"}
    if not preference:
        expected.add("muted")
    assert set(calls) == expected and len(calls) == len(expected)
    assert first["retry"] is True
    delivered = {
        str(uid("fcm-" + label)) for label in ("good", "unregistered", "delivered")
    }
    if not preference:
        delivered.add(str(uid("fcm-muted")))
    assert set(first["delivered"]) == delivered
    async with db.factory() as session:
        assert await session.get(DeviceToken, uid("fcm-unregistered")) is None
        assert await session.get(DeviceToken, uid("fcm-invalid")) is not None
        assert await session.get(DeviceToken, uid("fcm-foreign")) is not None
    retry = True
    calls.clear()
    second = await service.send_notification(
        user_id=uid("A"),
        notification_id="synthetic-notice",
        notification_type=kind,
        delivered=first["delivered"],
    )
    assert set(calls) == {"temporary", "invalid", "network"} and len(calls) == 3
    assert second["retry"] is False
    assert (
        len(second["delivered"]) == len(set(second["delivered"])) == len(delivered) + 3
    )
    evidence(
        "DLV04-device-" + kind,
        {
            "type": kind,
            "first_target_count": len(expected),
            "retry_target_count": 3,
            "foreign_and_previously_delivered_skipped": True,
            "unregistered_removed": True,
            "invalid_argument_retained": True,
            "retry_completed": True,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,preference", [TYPES[0], TYPES[2], TYPES[4]])
async def test_DLV04_account_preference_blocks_devices(
    db, monkeypatch, kind, preference
):
    async with db.factory() as session:
        session.add(ProductState(user_id=uid("A"), preferences={preference: False}))
        session.add(
            DeviceToken(
                id=uid("fcm-enabled"),
                user_id=uid("A"),
                token="synthetic-enabled",
                platform="android",
                preferences={preference: True},
            )
        )
        await session.commit()

    def forbidden(request):
        raise AssertionError("Account-disabled category reached HTTP transport")

    service = sender(db, monkeypatch, forbidden)
    result = await service.send_notification(
        user_id=uid("A"), notification_id="synthetic", notification_type=kind
    )
    assert result == {"delivered": [], "retry": False}
    evidence(
        "DLV04-account-" + kind, {"account_preference": preference, "http_calls": 0}
    )


@pytest.mark.asyncio
async def test_DLV04_receipt_survives_connection_reset_and_retries_only_pending_device(
    db, monkeypatch
):
    notice, receipt = uid("durable-notice"), uid("durable-receipt")
    async with db.factory() as session:
        session.add(
            AppNotification(
                id=notice, user_id=uid("A"), type="quintal_ready", payload={}
            )
        )
        await session.flush()
        session.add(
            HabitatTransaction(
                id=receipt,
                user_id=uid("A"),
                event_key="qpush:" + str(notice),
                category="quintal_push",
                amount=0,
                details={
                    "notification_id": str(notice),
                    "state": "pending",
                    "attempts": 0,
                    "delivered": [],
                    "next_attempt": 0,
                    "expires_at": (NOW + timedelta(hours=1)).timestamp(),
                },
            )
        )
        for label in ("good", "temporary"):
            session.add(
                DeviceToken(
                    id=uid("durable-" + label),
                    user_id=uid("A"),
                    token="synthetic-" + label,
                    platform="android",
                )
            )
        await session.commit()
    calls = []
    retry = False

    def transport(request):
        token = json.loads(request.content)["message"]["token"]
        calls.append(token)
        return httpx.Response(
            503 if token.endswith("temporary") and not retry else 200, json={}
        )

    service = sender(db, monkeypatch, transport)
    await deliver_pending(db.factory, now=NOW, sender=service)
    async with db.factory() as session:
        state = (await session.get(HabitatTransaction, receipt)).details
        assert state["state"] == "pending" and state["attempts"] == 1
        assert state["delivered"] == [str(uid("durable-good"))]
    await db.engine.dispose()
    calls.clear()
    retry = True
    await deliver_pending(db.factory, now=NOW + timedelta(seconds=59), sender=service)
    assert calls == []
    await deliver_pending(db.factory, now=NOW + timedelta(seconds=60), sender=service)
    assert calls == ["synthetic-temporary"]
    async with db.factory() as session:
        state = (await session.get(HabitatTransaction, receipt)).details
        assert state["state"] == "sent" and state["attempts"] == 2
        assert set(state["delivered"]) == {
            str(uid("durable-good")),
            str(uid("durable-temporary")),
        }
    await deliver_pending(db.factory, now=NOW + timedelta(minutes=5), sender=service)
    assert calls == ["synthetic-temporary"]
    evidence(
        "DLV04-durable-retry",
        {
            "database": "PostgreSQL",
            "pool_disposed_between_attempts": True,
            "first_delivered_devices": 1,
            "retry_only_pending_devices": 1,
            "retry_before_deadline_calls": 0,
            "persisted_attempts": 2,
            "final_state": "sent",
        },
    )
