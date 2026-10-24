"""Offline cryptographic verification using real RSA signatures and local JWKS."""

from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from app.services import apple_identity


@pytest.fixture
def signer(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update(kid="test-key", use="sig", alg="RS256")
    client = jwt.PyJWKClient("https://appleid.apple.com/auth/keys")
    monkeypatch.setattr(client, "fetch_data", lambda: {"keys": [jwk]})
    monkeypatch.setattr(apple_identity, "apple_keys", lambda: client)

    def sign(**changes):
        now = datetime.now(UTC)
        claims = dict(iss=apple_identity.APPLE_ISSUER, aud="test-client", sub="test-subject",
                      iat=now, exp=now + timedelta(minutes=5),
                      email="verified@example.com", email_verified="true")
        claims.update(changes)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})

    return sign


def test_verified_signature_and_claims(signer):
    claims = apple_identity.verify_apple_identity(signer(), "test-client")
    assert claims["sub"] == "test-subject"


@pytest.mark.parametrize("change", [
    {"iss": "https://appleid.apple.com.attacker.invalid"},
    {"aud": "other-client"}, {"exp": 1}, {"exp": None}, {"iat": None},
    {"sub": None}, {"sub": ""}, {"sub": "   "}, {"sub": 1},
    {"iat": datetime.now(UTC) + timedelta(hours=1)},
])
def test_signed_invalid_claims_rejected(signer, change):
    with pytest.raises(jwt.PyJWTError):
        apple_identity.verify_apple_identity(signer(**change), "test-client")


@pytest.mark.parametrize("algorithm", ["none", "HS256", "RS256"])
def test_forged_signatures_rejected(signer, algorithm):
    claims = jwt.decode(signer(), options={"verify_signature": False})
    key = (rsa.generate_private_key(public_exponent=65537, key_size=2048)
           if algorithm == "RS256" else "" if algorithm == "none" else "untrusted" * 8)
    token = jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "test-key"})
    with pytest.raises(jwt.PyJWTError):
        apple_identity.verify_apple_identity(token, "test-client")


def test_unknown_key_rejected(signer):
    parts = signer().split(".")
    import json
    parts[0] = jwt.utils.base64url_encode(json.dumps({"alg": "RS256", "kid": "unknown"}).encode()).decode()
    with pytest.raises(jwt.PyJWTError):
        apple_identity.verify_apple_identity(".".join(parts), "test-client")
