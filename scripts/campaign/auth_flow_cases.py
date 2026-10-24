"""SEC-07 exact boundaries and transaction races, using disposable accounts only."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select

pytest_plugins = ["security_cases", "extended_cases", "delivery_cases"]
from security_cases import NOW, evidence, otp, uid
from delivery_cases import code, messages
from app.config import get_settings
from app.persistence_models import EmailOtp, User, UserSession
from app.services import auth_service


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, 0, 1])
async def test_SEC07_expiry_microsecond_boundary(db, monkeypatch, offset):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    await otp(db, "boundary", expires=NOW + timedelta(microseconds=offset))
    async with db.factory() as session:
        if offset <= 0:
            with pytest.raises(HTTPException) as rejected:
                await auth_service.consume_otp(
                    session, email="a@example.com", purpose="boundary", code="123456"
                )
            assert rejected.value.status_code == 400
        else:
            row = await auth_service.consume_otp(
                session, email="a@example.com", purpose="boundary", code="123456"
            )
            assert row.consumed_at == NOW
            await session.commit()
    evidence(
        f"SEC07-expiry-{offset}",
        {
            "scenario": "SEC-07",
            "expiry_offset_microseconds": offset,
            "accepted": offset > 0,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [59.999, 60.0])
async def test_SEC07_resend_exact_cooldown_and_previous_code_invalidation(
    db, monkeypatch, offset
):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    monkeypatch.setattr(auth_service, "_send_otp", AsyncMock())
    monkeypatch.setattr(auth_service.secrets, "randbelow", lambda limit: 111111)
    async with db.factory() as session:
        await auth_service.create_otp(
            session, email="a@example.com", purpose="exact", user_id=uid("A")
        )
        first = await session.scalar(select(EmailOtp))
        first.created_at = NOW
        await session.commit()
    current = NOW + timedelta(seconds=offset)
    monkeypatch.setattr(auth_service, "utc_now", lambda: current)
    monkeypatch.setattr(auth_service.secrets, "randbelow", lambda limit: 222222)
    async with db.factory() as session:
        if offset < 60:
            with pytest.raises(HTTPException) as rejected:
                await auth_service.create_otp(
                    session, email="a@example.com", purpose="exact", user_id=uid("A")
                )
            assert rejected.value.status_code == 429
        else:
            await auth_service.create_otp(
                session, email="a@example.com", purpose="exact", user_id=uid("A")
            )
            latest = await session.scalar(
                select(EmailOtp).where(EmailOtp.consumed_at.is_(None))
            )
            latest.created_at = current
            await session.commit()
    if offset == 60:
        async with db.factory() as session:
            with pytest.raises(HTTPException):
                await auth_service.consume_otp(
                    session, email="a@example.com", purpose="exact", code="111111"
                )
        async with db.factory() as session:
            assert (
                await auth_service.consume_otp(
                    session, email="a@example.com", purpose="exact", code="222222"
                )
            ).consumed_at == current
            await session.commit()
    evidence(
        f"SEC07-cooldown-{offset}",
        {
            "scenario": "SEC-07",
            "seconds": offset,
            "resend_allowed": offset == 60,
            "old_code_invalidated_when_replaced": offset == 60,
            "clock": "controlled application clock; fixture timestamps aligned",
        },
    )


@pytest.mark.asyncio
async def test_SEC07_concurrent_consumption_is_single_use(db, monkeypatch):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    await otp(db, "single")

    async def consume():
        async with db.factory() as session:
            try:
                await auth_service.consume_otp(
                    session, email="a@example.com", purpose="single", code="123456"
                )
                await session.commit()
                return 200
            except HTTPException as error:
                await session.rollback()
                return error.status_code

    statuses = await asyncio.gather(consume(), consume())
    assert sorted(statuses) == [200, 400]
    evidence(
        "SEC07-concurrent-consume",
        {
            "scenario": "SEC-07",
            "statuses": sorted(statuses),
            "successful_consumptions": 1,
        },
    )


@pytest.mark.asyncio
async def test_SEC07_concurrent_bad_codes_cannot_lose_attempts(db, monkeypatch):
    monkeypatch.setattr(auth_service, "utc_now", lambda: NOW)
    limit = get_settings().otp_max_attempts
    row_id = await otp(db, "attempt-race", attempts=limit - 1)

    async def bad():
        async with db.factory() as session:
            with pytest.raises(HTTPException) as denied:
                await auth_service.consume_otp(
                    session,
                    email="a@example.com",
                    purpose="attempt-race",
                    code="000000",
                )
            await session.rollback()
            return denied.value.status_code

    assert await asyncio.gather(bad(), bad()) == [400, 400]
    async with db.factory() as session:
        assert (await session.get(EmailOtp, row_id)).attempts == limit
        with pytest.raises(HTTPException):
            await auth_service.consume_otp(
                session, email="a@example.com", purpose="attempt-race", code="123456"
            )
    evidence(
        "SEC07-attempt-race",
        {
            "scenario": "SEC-07",
            "parallel_bad_codes": 2,
            "attempts_at_limit": limit,
            "valid_code_after_limit_rejected": True,
        },
    )


@pytest.mark.asyncio
async def test_SEC07_concurrent_refresh_has_one_winner_and_revokes_old_token(db):
    async with db.factory() as session:
        bundle = await auth_service.issue_tokens(
            session, user=db.users["A"], device_id="refresh-race"
        )
        await session.commit()
        before = await session.scalar(select(func.count()).select_from(UserSession))
    results = await asyncio.gather(
        *(
            db.client.post(
                "/auth/refresh", json={"refresh_token": bundle["refresh_token"]}
            )
            for _ in range(2)
        )
    )
    assert sorted(r.status_code for r in results) == [200, 401]
    fresh = next(r.json() for r in results if r.status_code == 200)
    assert (
        await db.client.get(
            "/sync/pull", headers={"Authorization": "Bearer " + fresh["access_token"]}
        )
    ).status_code == 200
    assert (
        await db.client.get(
            "/sync/pull", headers={"Authorization": "Bearer " + bundle["access_token"]}
        )
    ).status_code == 401
    assert (
        await db.client.get("/sync/pull", headers=db.headers("B"))
    ).status_code == 200
    async with db.factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(UserSession))
            == before + 1
        )
    evidence(
        "SEC07-refresh-race",
        {
            "scenario": "SEC-07",
            "statuses": [200, 401],
            "new_sessions": 1,
            "old_access_rejected": True,
            "other_account_valid": True,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["same-email", "normalized-email"])
async def test_SEC07_parallel_first_request_obeys_cooldown_and_sends_one_email(
    db, mailpit, smtp_settings, monkeypatch, variant
):
    async with db.factory() as session:
        user = await session.get(User, uid("A"))
        user.password_hash = auth_service.password_hash.hash("Synthetic_OTP_only_2026!")
        await session.commit()
    original = auth_service._send_otp
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        await original(**kwargs)

    monkeypatch.setattr(auth_service, "_send_otp", delayed)
    first = asyncio.create_task(
        db.client.post("/auth/password-reset/request", json={"email": "a@example.com"})
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(
            db.client.post(
                "/auth/password-reset/request",
                json={
                    "email": "A@EXAMPLE.COM"
                    if variant == "normalized-email"
                    else "a@example.com"
                },
            )
        )
        await asyncio.sleep(0.3)
    finally:
        release.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
    statuses = sorted(r.status_code for r in responses)
    sent = messages(mailpit)
    async with db.factory() as session:
        active = await session.scalar(
            select(func.count())
            .select_from(EmailOtp)
            .where(
                EmailOtp.email == "a@example.com",
                EmailOtp.purpose == "reset_password",
                EmailOtp.consumed_at.is_(None),
            )
        )
    evidence(
        "SEC07-first-request-race-" + variant,
        {
            "scenario": "SEC-07",
            "variant": variant,
            "statuses": statuses,
            "smtp_messages": len(sent),
            "active_otps": active,
            "scope": "Two requests; first delivery held 300 ms before commit. Own PostgreSQL and Mailpit; not load or a latency benchmark.",
        },
    )
    assert statuses == [202, 429] and len(sent) == active == 1
    delivered_code = code(mailpit, sent[0])
    verified = await db.client.post(
        "/auth/password-reset/verify",
        json={"email": "a@example.com", "code": delivered_code},
    )
    assert verified.status_code == 200
