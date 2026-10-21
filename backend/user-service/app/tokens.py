from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

import jwt

from app.config import settings


@cache
def _private_key() -> str:
    return Path(settings.jwt_private_key_path).read_text()


@cache
def _public_key() -> str:
    return Path(settings.jwt_public_key_path).read_text()


def create_access_token(user_id: int, role: str) -> str:
    """Signs a token with the private key. Only user-service can do this."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(claims, _private_key(), algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Verifies signature, expiration, issuer and audience.

    Raises jwt.InvalidTokenError (or a subclass) if anything is wrong.
    The accepted algorithm is fixed, so a token cannot choose its own.
    """
    return jwt.decode(
        token,
        _public_key(),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "role", "exp", "iss", "aud"]},
    )
