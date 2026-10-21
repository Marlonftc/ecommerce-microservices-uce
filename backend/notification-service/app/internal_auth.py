import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import settings


def require_internal_key(x_internal_key: Annotated[str | None, Header()] = None) -> None:
    """Only other microservices know this key. The gateway never routes /internal.

    compare_digest takes the same time whatever the input, so the key cannot
    be guessed character by character by measuring response times.
    """
    if x_internal_key is None or not hmac.compare_digest(x_internal_key, settings.internal_api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Internal access only")


InternalOnly = Depends(require_internal_key)
