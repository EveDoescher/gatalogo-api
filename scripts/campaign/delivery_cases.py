"""Disposable SMTP recovery, ambiguous acceptance, cooldown and responsiveness."""

import asyncio
import re
import smtplib
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select

pytest.register_assert_rewrite("security_cases", "extended_cases")
pytest_plugins = ["security_cases", "extended_cases"]
from security_cases import evidence, uid
from app.config import get_settings
from app.persistence_models import EmailOtp, User
from app.services import auth_service


@pytest.fixture
def smtp_settings(mailpit, monkeypatch):
    settings = get_settings()
    for key, value in {
        "smtp_enabled": True,
        "smtp_host": "127.0.0.1",
        "smtp_port": mailpit["port"],
        "smtp_use_tls": False,
        "smtp_username": None,
        "smtp_password": None,
        "smtp_from": "campaign@example.com",
    }.items():
        monkeypatch.setattr(settings, key, value)
    return settings


def messages(mailpit):
    return mailpit["get"]("/api/v1/messages")["messages"]


def code(mailpit, message):
    content = mailpit["get"]("/api/v1/message/" + message["ID"])
    codes = re.findall(r"\b[0-9]{6}\b", content["Text"])
    assert len(codes) == 1
    return codes[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["confirm", "grant"])
async def test_DLV01_SEC07_smtp_password_recovery_complete_and_replay(
    db, mailpit, smtp_settings, flow
):
    old, new = "Synthetic_original_2026!", "Synthetic_replacement_2026!"
    async with db.factory() as session:
        user = await session.get(User, uid("A"))
        user.password_hash = auth_service.password_hash.hash(old)
        await session.commit()
    unknown = await db.client.post(
        "/auth/password-reset/request", json={"email": "absent@example.com"}
    )
    started_at = auth_service.utc_now()
    started = time.perf_counter()
    request = await db.client.post(
        "/auth/password-reset/request", json={"email": "a@example.com"}
    )
    assert (
        unknown.status_code == request.status_code == 202
        and unknown.json() == request.json()
    )
    sent = messages(mailpit)
    observed_ms = (time.perf_counter() - started) * 1000
    observed_at = auth_service.utc_now()
    assert len(sent) == 1
    content = mailpit['get']('/api/v1/message/' + sent[0]['ID'])
    assert any(m['Address'] == 'a@example.com' for m in content['To'])
    async with db.factory() as session:
        otp_row = await session.scalar(select(EmailOtp).where(EmailOtp.email == 'a@example.com'))
        assert otp_row.purpose == 'reset_password' and otp_row.consumed_at is None
        ttl = timedelta(minutes=smtp_settings.otp_ttl_minutes)
        assert started_at + ttl <= otp_row.expires_at <= observed_at + ttl
    otp = code(mailpit, sent[0])
    assert (
        await db.client.post(
            "/auth/password-reset/request", json={"email": "a@example.com"}
        )
    ).status_code == 429
    if flow == "confirm":
        body = {"email": "a@example.com", "code": otp, "password": new}
        endpoint = "/auth/password-reset/confirm"
    else:
        grant = await db.client.post(
            "/auth/password-reset/verify", json={"email": "a@example.com", "code": otp}
        )
        assert grant.status_code == 200
        assert (
            await db.client.get(
                "/auth/me",
                headers={"Authorization": "Bearer " + grant.json()["reset_token"]},
            )
        ).status_code == 401
        assert (
            await db.client.post(
                "/auth/password-reset/verify",
                json={"email": "a@example.com", "code": otp},
            )
        ).status_code == 400
        body = {"reset_token": grant.json()["reset_token"], "password": new}
        endpoint = "/auth/password-reset/complete"
    assert (await db.client.post(endpoint, json=body)).status_code == 204
    assert (await db.client.post(endpoint, json=body)).status_code == 400
    assert (await db.client.get("/auth/me", headers=db.headers("A"))).status_code == 401
    assert (await db.client.get("/auth/me", headers=db.headers("B"))).status_code == 200
    assert (
        await db.client.post(
            "/auth/login", json={"email": "a@example.com", "password": old}
        )
    ).status_code == 401
    assert (
        await db.client.post(
            "/auth/login", json={"email": "a@example.com", "password": new}
        )
    ).status_code == 200
    assert len(messages(mailpit)) == 1
    evidence(
        "DLV01-recovery-" + flow,
        {
            "scenario": "DLV-01",
            "flow": flow,
            "messages": 1,
            "recipient_matches": True,
            "purpose": "reset_password",
            "configured_expiration_verified": True,
            "request_to_mailpit_observed_ms": observed_ms,
            "old_password_rejected": True,
            "old_sessions_revoked": True,
            "other_account_unchanged": True,
            "single_use": True,
            "cooldown": 429,
            "scope": "real disposable SMTP; no code/password/token in evidence",
        },
    )


@pytest.mark.asyncio
async def test_DLV02_SMTP_ambiguous_acceptance_retry_and_cooldown(
    db, mailpit, smtp_settings, monkeypatch
):
    real = auth_service.smtplib.SMTP

    class LostAcknowledgment(real):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            raise smtplib.SMTPServerDisconnected(
                "controlled acknowledgment loss after acceptance"
            )

    monkeypatch.setattr(auth_service.smtplib, "SMTP", LostAcknowledgment)
    async with db.factory() as session:
        with pytest.raises(smtplib.SMTPServerDisconnected):
            await auth_service.create_otp(
                session,
                email="a@example.com",
                purpose="reset_password",
                user_id=uid("A"),
            )
        await session.rollback()
    assert len(messages(mailpit)) == 1
    async with db.factory() as session:
        assert await session.scalar(select(func.count()).select_from(EmailOtp)) == 0
    monkeypatch.setattr(auth_service.smtplib, "SMTP", real)
    async with db.factory() as session:
        await auth_service.create_otp(
            session, email="a@example.com", purpose="reset_password", user_id=uid("A")
        )
        await session.commit()
    assert len(messages(mailpit)) == 2
    async with db.factory() as session:
        with pytest.raises(Exception) as rejected:
            await auth_service.create_otp(
                session,
                email="a@example.com",
                purpose="reset_password",
                user_id=uid("A"),
            )
        assert rejected.value.status_code == 429
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(EmailOtp)) == 1
    evidence(
        "DLV02-ambiguous-acceptance",
        {
            "scenario": "DLV-02",
            "accepted_messages": 2,
            "persisted_otps": 1,
            "immediate_retry_after_rollback": "allowed",
            "retry_after_commit": 429,
            "finding": "Acceptance uncertainty plus rollback permits a second email immediately; first code has no persisted record",
            "scope": "real SMTP delivery followed by injected client exception, not a physical network outage",
        },
    )


@pytest.mark.asyncio
async def test_DLV02_LAT08_slow_SMTP_does_not_block_event_loop(
    db, mailpit, smtp_settings, monkeypatch
):
    entered, released = threading.Event(), threading.Event()
    real = auth_service.smtplib.SMTP

    class Paused(real):
        def send_message(self, *args, **kwargs):
            entered.set()
            if not released.wait(5):
                raise TimeoutError("test barrier expired")
            return super().send_message(*args, **kwargs)

    monkeypatch.setattr(auth_service.smtplib, "SMTP", Paused)

    async def send():
        async with db.factory() as session:
            await auth_service.create_otp(
                session,
                email="a@example.com",
                purpose="reset_password",
                user_id=uid("A"),
            )
            await session.commit()

    pending = asyncio.create_task(send())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        health = await asyncio.wait_for(db.client.get("/health"), 2)
        pull = await asyncio.wait_for(
            db.client.get("/sync/pull", headers=db.headers("B")), 2
        )
        assert health.status_code == pull.status_code == 200 and not pending.done()
        held = db.engine.pool.checkedout()
    finally:
        released.set()
        await asyncio.wait_for(pending, 5)
    assert len(messages(mailpit)) == 1
    evidence(
        "DLV02-slow-smtp",
        {
            "scenario": "DLV-02",
            "parallel_health": 200,
            "parallel_pull": 200,
            "smtp_was_still_paused": True,
            "pool_checked_out_during_send": held,
            "scope": "bounded barrier; responsiveness proof, not latency benchmark",
        },
    )


@pytest.mark.asyncio
async def test_SEC08_SMTP_enabled_does_not_log_otp(db, mailpit, smtp_settings, caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="app.services.auth_service"):
        async with db.factory() as session:
            await auth_service.create_otp(
                session,
                email="a@example.com",
                purpose="reset_password",
                user_id=uid("A"),
            )
            await session.commit()
    secret = code(mailpit, messages(mailpit)[0])
    application_logs = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("app.")
    )
    assert secret not in application_logs
    evidence(
        "SEC08-smtp-log",
        {
            "scenario": "SEC-08",
            "mode": "smtp_enabled",
            "application_log_contains_otp": False,
            "scope": "captured app log records only; reverse-proxy/APM/deployment logs not observed",
        },
    )
