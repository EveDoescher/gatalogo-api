"""Verify Apple identity tokens before using any identity or email claims."""

from functools import lru_cache

import jwt

APPLE_ISSUER = "https://appleid.apple.com"


@lru_cache(maxsize=1)
def apple_keys() -> jwt.PyJWKClient:
    # The endpoint is fixed; neither token headers nor clients choose its URL.
    return jwt.PyJWKClient(APPLE_ISSUER + "/auth/keys", timeout=5)


def verify_apple_identity(token: str, audience: str) -> dict:
    if jwt.get_unverified_header(token).get("alg") != "RS256":
        raise jwt.InvalidAlgorithmError("Unsupported Apple signing algorithm")
    key = apple_keys().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=APPLE_ISSUER,
        options={"require": ["iss", "aud", "exp", "iat", "sub"]},
    )
    if not isinstance(claims["sub"], str) or not claims["sub"].strip():
        raise jwt.InvalidTokenError("Missing Apple subject")
    return claims
