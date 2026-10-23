"""Negative identity-provider contracts on synthetic accounts in disposable schemas."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from fixtures import uid

pytest_plugins = ["security_cases"]
from security_cases import evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["unsigned", "wrong-signature", "expired", "issuer-substring"]
)
async def test_SEC01_apple_rejects_unverified_identity_for_existing_synthetic_account(
    db, variant
):
    original = await db.client.get(
        "/sync/pull?cursor=0&limit=1", headers=db.headers("B")
    )
    assert original.status_code == 200
    now = datetime.now(UTC)
    claims = {
        "iss": "https://appleid.apple.com"
        if variant != "issuer-substring"
        else "https://not-appleid.apple.com.attacker.invalid",
        "sub": "synthetic-identity-" + variant,
        "email": db.users["B"].email,
        "aud": "synthetic-wrong-audience",
        "iat": now,
        "exp": now
        + (timedelta(seconds=-60) if variant == "expired" else timedelta(minutes=5)),
    }
    unsigned = variant in {"unsigned", "issuer-substring"}
    token = jwt.encode(
        claims,
        "" if unsigned else "synthetic-untrusted-signing-key",
        algorithm="none" if unsigned else "HS256",
    )
    response = await db.client.post(
        "/auth/apple",
        json={"id_token": token, "device_id": "campaign-identity-negative"},
    )
    authenticated = None
    same_account = False
    if response.status_code == 200:
        bundle = response.json()
        same_account = bundle["user_id"] == str(uid("B"))
        authenticated = (
            await db.client.get(
                "/sync/pull?cursor=0&limit=1",
                headers={"Authorization": "Bearer " + bundle["access_token"]},
            )
        ).status_code
    evidence(
        "SEC01-apple-" + variant,
        {
            "scenario": "SEC-01",
            "variant": variant,
            "expected_rejection": 401,
            "actual_status": response.status_code,
            "issued_session_for_existing_synthetic_account": same_account,
            "subsequent_authenticated_sync_status": authenticated,
            "finding": "Unverified identity token accepted and linked by supplied email"
            if same_account
            else None,
            "scope": "own synthetic accounts and disposable schemas only; no Apple/network/real-user token or credential used; negative assertion remains red",
        },
    )
    assert response.status_code == 401, (
        "Unverified external identity must not issue an application session"
    )
