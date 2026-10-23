"""Provider route controls with an explicit trusted-verifier double, never a live IdP."""

import pytest
from sqlalchemy import func, select

pytest_plugins = ["security_cases"]
from security_cases import evidence, uid
from app.config import get_settings
from app.persistence_models import AuthIdentity, User, UserSession
from app.routers import auth


async def counts(factory):
    async with factory() as session:
        return [
            await session.scalar(select(func.count()).select_from(model))
            for model in [User, AuthIdentity, UserSession]
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["verifier-reject", "issuer-wrong", "email-unverified", "provider-error"]
)
async def test_SEC01_google_rejections_preserve_account_identity_and_sessions(
    db, monkeypatch, variant
):
    monkeypatch.setattr(get_settings(), "google_web_client_id", "synthetic-client")
    before = await counts(db.factory)
    calls = []

    def verify(token, request, audience):
        calls.append(audience)
        if variant == "verifier-reject":
            raise ValueError("synthetic invalid signature")
        if variant == "provider-error":
            raise TimeoutError("synthetic provider unavailable")
        return {
            "iss": "https://wrong.example.invalid"
            if variant == "issuer-wrong"
            else "https://accounts.google.com",
            "email_verified": variant != "email-unverified",
            "email": db.users["B"].email,
            "sub": "synthetic-google-subject",
        }

    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", verify)
    response = await db.client.post(
        "/auth/google",
        json={"id_token": "synthetic-provider-token-000000", "device_id": "campaign"},
    )
    assert response.status_code == 401 and calls == ["synthetic-client"]
    assert await counts(db.factory) == before
    assert (
        await db.client.get("/sync/pull?cursor=0&limit=1", headers=db.headers("B"))
    ).status_code == 200
    evidence(
        "SEC01-google-" + variant,
        {
            "scenario": "SEC-01",
            "variant": variant,
            "status": 401,
            "verifier_calls": 1,
            "user_identity_session_counts_unchanged": True,
            "existing_session_usable": True,
            "scope": "trusted-verifier boundary double; no network/Google keys; provider-error currently maps to401, not a recommendation",
        },
    )


@pytest.mark.asyncio
async def test_SEC01_google_missing_configuration_does_not_call_verifier(
    db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "google_web_client_id", "")

    def forbidden(*args, **kwargs):
        pytest.fail("Unconfigured provider must not perform verification/network")

    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", forbidden)
    before = await counts(db.factory)
    result = await db.client.post(
        "/auth/google", json={"id_token": "synthetic-provider-token-000000"}
    )
    assert result.status_code == 503 and await counts(db.factory) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "deactivated"])
async def test_SEC01_google_verified_boundary_reuses_identity_and_existing_synthetic_account(
    db, monkeypatch, status
):
    monkeypatch.setattr(get_settings(), "google_web_client_id", "synthetic-client")
    async with db.factory() as session:
        user = await session.get(User, uid("B"))
        user.status = status
        await session.commit()
    before = await counts(db.factory)
    seen = []

    def verified(token, request, audience):
        seen.append(audience)
        return {
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "email": db.users["B"].email,
            "sub": "synthetic-trusted-google-subject",
        }

    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", verified)
    for _ in range(2):
        response = await db.client.post(
            "/auth/google",
            json={
                "id_token": "synthetic-provider-token-000000",
                "device_id": "campaign",
            },
        )
        assert response.status_code == 200 and response.json()["user_id"] == str(
            uid("B")
        )
        authenticated = await db.client.get(
            "/sync/pull?cursor=0&limit=1",
            headers={"Authorization": "Bearer " + response.json()["access_token"]},
        )
        assert authenticated.status_code == 200
    after = await counts(db.factory)
    assert seen == ["synthetic-client"] * 2
    assert after == [before[0], before[1] + 1, before[2] + 2]
    async with db.factory() as session:
        user = await session.get(User, uid("B"))
        assert user.status == "active"
        identity = (
            await session.execute(
                select(AuthIdentity).where(
                    AuthIdentity.provider == "google",
                    AuthIdentity.subject == "synthetic-trusted-google-subject",
                )
            )
        ).scalar_one()
        assert identity.user_id == user.id
    evidence(
        "SEC01-google-positive-" + status,
        {
            "scenario": "SEC-01",
            "initial_status": status,
            "status": 200,
            "verifier_audience_passed": True,
            "new_users": 0,
            "new_identities": 1,
            "new_sessions": 2,
            "repeated_subject_reused": True,
            "scope": "verified-claims boundary simulated; tests current route/reactivation rules, not live Google cryptography or a proposed Apple correction",
        },
    )
