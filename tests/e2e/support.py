"""Helpers for the end-to-end tests: real services in Docker, reached through
the 127.0.0.1 ports of compose.override.example.yaml. product-service has no
published port (it can be scaled), so it is reached through the gateway.

Credentials: the admin account is read from the root .env (never printed);
customers are created for each test with random passwords kept in memory.
"""
import json
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx2
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV = dotenv_values(ROOT / ".env")
COMPOSE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.override.example.yaml",
           "-f", "compose.e2e.yaml"]
PORTS = {"user": 8001, "inventory": 8003, "payment": 8004, "notification": 8005, "order": 8006}
GATEWAY = f"http://127.0.0.1:{ENV.get('GATEWAY_PORT') or 8080}"


def http(service: str) -> httpx2.Client:
    if service == "product":
        return httpx2.Client(base_url=f"{GATEWAY}/api", timeout=60)
    return httpx2.Client(base_url=f"http://127.0.0.1:{PORTS[service]}", timeout=60)


# --- Docker ------------------------------------------------------------------------------

def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([*COMPOSE, *args], cwd=ROOT, capture_output=True, text=True, timeout=300)
    if check and result.returncode != 0:
        raise AssertionError(f"docker compose {' '.join(args)} failed:\n{result.stderr[-2000:]}")
    return result


def containers(service: str) -> list[dict]:
    out = compose("ps", "-a", "--format", "json", service).stdout.strip()
    if not out:
        return []
    if out.startswith("["):
        return json.loads(out)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def wait_healthy(service: str, count: int = 1, timeout: float = 120) -> None:
    wait_until(lambda: len([c for c in containers(service)
                            if c.get("State") == "running" and c.get("Health") == "healthy"]) == count,
               timeout, what=f"{count} healthy {service}")


def wait_until(check, timeout: float, interval: float = 0.5, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timeout after {timeout}s waiting for {what}")


# --- users ----------------------------------------------------------------------------

@dataclass
class User:
    id: int
    headers: dict


def login(email: str, password: str) -> dict:
    with http("user") as c:
        response = c.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, "login failed"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def admin() -> User:
    headers = login(ENV["ADMIN_EMAIL"], ENV["ADMIN_PASSWORD"])
    with http("user") as c:
        return User(c.get("/users/me", headers=headers).json()["id"], headers)


def new_customer() -> User:
    email = f"e2e-{uuid.uuid4().hex[:12]}@example.com"
    password = secrets.token_urlsafe(18)  # only in memory, never printed
    with http("user") as c:
        created = c.post("/auth/register", json={"email": email, "full_name": "Cliente E2E",
                                                 "password": password})
    assert created.status_code == 201, created.text
    return User(created.json()["id"], login(email, password))


# --- catalogue and stock ------------------------------------------------------------------

def new_product(adm: User, stock: int, price: str = "25.50") -> int:
    """A product only for this test, so existing data is never changed."""
    sku = f"E2E-{uuid.uuid4().hex[:10].upper()}"
    with http("product") as c:
        product = c.post("/products", headers=adm.headers, json={
            "sku": sku, "name": f"Producto de prueba {sku}", "category": "pruebas", "price": price})
    assert product.status_code == 201, product.text
    product_id = product.json()["id"]
    with http("inventory") as c:
        adjusted = c.post(f"/inventory/{product_id}/adjustments", json={"delta": stock, "reason": "e2e"},
                          headers={**adm.headers, "Idempotency-Key": f"e2e-{uuid.uuid4()}"})
    assert adjusted.status_code == 201, adjusted.text
    return product_id


def stock(adm: User, product_id: int) -> tuple[int, int]:
    with http("inventory") as c:
        data = c.get(f"/inventory/{product_id}", headers=adm.headers).json()
    return data["available"], data["reserved"]


# --- orders ------------------------------------------------------------------------------

def checkout(user: User, product_id: int, quantity: int = 1) -> httpx2.Response:
    with http("order") as c:
        version = c.put(f"/cart/items/{product_id}", json={"quantity": quantity},
                        headers=user.headers).json()["version"]
        return c.post("/orders", json={"cart_version": version},
                      headers={**user.headers, "Idempotency-Key": f"buy-{uuid.uuid4()}"})


def pay(user: User, order_id: str, simulation: str = "APPROVE") -> httpx2.Response:
    with http("order") as c:
        return c.post(f"/orders/{order_id}/pay", json={"simulation": simulation},
                      headers={**user.headers, "Idempotency-Key": f"pay-{order_id}"})


def cancel(user: User, order_id: str) -> httpx2.Response:
    with http("order") as c:
        return c.post(f"/orders/{order_id}/cancel", headers=user.headers)


def order(user: User, order_id: str) -> dict:
    with http("order") as c:
        return c.get(f"/orders/{order_id}", headers=user.headers).json()


def cart(user: User) -> dict:
    with http("order") as c:
        return c.get("/cart", headers=user.headers).json()


def operations(adm: User, order_id: str) -> dict[str, dict]:
    """{"RESOLVE_PAYMENT": {...}, "ORDER_PAID": {...}, ...} of one order."""
    with http("order") as c:
        page = c.get("/admin/operations", params={"order_id": order_id, "size": 100},
                     headers=adm.headers).json()
    return {op["operation_key"].split(":", 1)[1]: op for op in page["items"]}


def notifications(user: User, event_type: str, order_id: str) -> list[dict]:
    with http("notification") as c:
        items = c.get("/notifications", params={"size": 100}, headers=user.headers).json()["items"]
    return [n for n in items if n["type"] == event_type and n["order_id"] == order_id]


def payments(user: User) -> list[dict]:
    with http("payment") as c:
        return c.get("/payments", headers=user.headers).json()
