"""Checks that the initial stock matches the initial catalog of product-service.

Run inside the inventory-service container:
    docker compose exec inventory-service python -m app.check_catalog

It calls product-service through the Docker network, by service name.
Exit code 0 = everything matches, 1 = a difference or product-service down.
"""
import logging
import sys

import httpx2

from app.config import settings
from app.seed import INITIAL_STOCK


def main() -> int:
    # Show only this script's results, not one log line per HTTP request
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    timeout = httpx2.Timeout(settings.http_read_timeout_seconds,
                             connect=settings.http_connect_timeout_seconds)
    headers = {"X-Internal-Key": settings.internal_api_key}
    print(f"product-service: {settings.product_service_url}")
    errors = 0

    for item in INITIAL_STOCK:
        url = f"{settings.product_service_url}/internal/products/{item['product_id']}"
        try:
            response = httpx2.get(url, headers=headers, timeout=timeout)
        except httpx2.HTTPError as exc:
            print(f"ERROR product-service not reachable: {exc}")
            return 1

        if response.status_code != 200:
            print(f"FAIL  id {item['product_id']}: product-service answered {response.status_code}")
            errors += 1
            continue

        sku = response.json()["sku"]
        ok = sku == item["sku"]
        errors += not ok
        print(f"{'OK  ' if ok else 'FAIL'}  id {item['product_id']}: expected {item['sku']}, product-service has {sku}")

    print("All initial products match." if errors == 0 else f"{errors} difference(s) found.")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
