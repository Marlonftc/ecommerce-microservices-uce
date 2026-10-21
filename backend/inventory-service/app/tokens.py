from functools import cache
from pathlib import Path

import jwt

from app.config import settings


@cache
def _public_key() -> str:
    return Path(settings.jwt_public_key_path).read_text()


def decode_access_token(token: str) -> dict:
    """Verifies a token issued by user-service.

    Only the public key is used: this service can check tokens but cannot
    create them. Raises jwt.InvalidTokenError if anything is wrong.
    """
    return jwt.decode(
        token,
        _public_key(),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "role", "exp", "iss", "aud"]},
    )
