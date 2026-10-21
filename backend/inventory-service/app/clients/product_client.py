"""Calls to product-service. Used only to validate products on stock adjustments."""
import logging

import httpx2

from app.config import settings

logger = logging.getLogger(__name__)


class ProductServiceUnavailable(Exception):
    """product-service did not answer, or answered with an unexpected error."""


def product_exists(product_id: int) -> bool:
    """True if the product exists (active or not), False if product-service says 404."""
    url = f"{settings.product_service_url}/internal/products/{product_id}"
    timeout = httpx2.Timeout(settings.http_read_timeout_seconds,
                             connect=settings.http_connect_timeout_seconds)
    headers = {"X-Internal-Key": settings.internal_api_key}

    # A GET does not change anything, so one retry is safe. Only connection
    # errors are retried: a slow answer is not retried to avoid waiting twice.
    for attempt in (1, 2):
        try:
            response = httpx2.get(url, headers=headers, timeout=timeout)
            break
        except httpx2.ConnectError as exc:
            logger.warning("product-service connection failed (attempt %d): %s", attempt, exc)
            if attempt == 2:
                raise ProductServiceUnavailable from exc
        except httpx2.HTTPError as exc:
            logger.warning("product-service request failed: %s", exc)
            raise ProductServiceUnavailable from exc

    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    logger.warning("product-service answered %s", response.status_code)
    raise ProductServiceUnavailable(f"unexpected status {response.status_code}")
