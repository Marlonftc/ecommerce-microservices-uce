from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.tokens import decode_access_token

bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


@dataclass(frozen=True)
class TokenUser:
    """The user as described by the token. No database lookup is made."""

    id: int
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_optional_user(credentials: Credentials) -> TokenUser | None:
    """Public endpoints: no token is fine, but a token that is sent must be valid."""
    if credentials is None:
        return None
    try:
        claims = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid or expired token")
    return TokenUser(id=int(claims["sub"]), role=claims["role"])


OptionalUser = Annotated[TokenUser | None, Depends(get_optional_user)]


def require_admin(user: OptionalUser) -> TokenUser:
    if user is None:
        raise _unauthorized("Not authenticated")
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


AdminUser = Annotated[TokenUser, Depends(require_admin)]
