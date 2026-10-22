"""Helpers for the gateway tests: every HTTP call goes to http://127.0.0.1:8080
(the only published port of compose.yaml). Docker commands are used to read
the gateway log, inspect networks and scale, stop or pause containers.

Credentials: the admin account is read from the root .env (never printed);
customers are created with random passwords kept only in memory.
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
GATEWAY = f"http://127.0.0.1:{ENV.get('GATEWAY_PORT') or 8080}"
COMPOSE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.override.example.yaml"]
SERVICES = ["user-service", "product-service", "inventory-service", "order-service",
            "payment-service", "notification-service"]
NOT_FOUND = {"detail": {"code": "NOT_FOUND", "message": "Route not found"}}


def gw(**kwargs) -> httpx2.Client:
    return httpx2.Client(base_url=GATEWAY, timeout=kwargs.pop("timeout", 60), **kwargs)


def rid(label: str) -> str:
    """A unique request id, accepted by the gateway (letters, digits, dashes)."""
    return f"gwt-{label}-{uuid.uuid4().hex[:12]}"


# --- Docker ------------------------------------------------------------------------------

def run(*cmd: str, check: bool = True, timeout: float = 300) -> subprocess.CompletedProcess:
    result = subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"{' '.join(cmd[:4])}... failed:\n{result.stderr[-2000:]}")
    return result


def compose(*args: str, check: bool = True, timeout: float = 300) -> subprocess.CompletedProcess:
    return run(*COMPOSE, *args, check=check, timeout=timeout)


def containers(service: str) -> list[dict]:
    out = compose("ps", "-a", "--format", "json", service).stdout.strip()
    if not out:
        return []
    if out.startswith("["):
        return json.loads(out)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def running(service: str) -> list[dict]:
    return [c for c in containers(service) if c.get("State") == "running"]


def inspect(container: str) -> dict:
    return json.loads(run("docker", "inspect", container).stdout)[0]


def ips(service: str) -> dict[str, str]:
    """{ip on ecommerce-net: container name} of the running containers of a service."""
    result = {}
    for c in running(service):
        for name, net in inspect(c["Name"])["NetworkSettings"]["Networks"].items():
            if name.endswith("_ecommerce-net") and net.get("IPAddress"):
                result[net["IPAddress"]] = c["Name"]
    return result


def wait_until(check, timeout: float, interval: float = 0.5, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timeout after {timeout}s waiting for {what}")


def wait_healthy(service: str, count: int = 1, timeout: float = 120) -> None:
    wait_until(lambda: len([c for c in running(service) if c.get("Health") == "healthy"]) == count,
               timeout, what=f"{count} healthy {service}")


def gateway_log(since: str = "15m") -> dict[str, dict]:
    """Access log of the gateway as {request_id: entry}. Error-log lines are skipped."""
    out = compose("logs", "--no-log-prefix", "--since", since, "gateway").stdout
    entries = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries[entry.get("request_id")] = entry
    return entries


def utc_now() -> str:
    """Timestamp accepted by "docker compose logs --since" (1 s earlier, to be safe)."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def gateway_errors(since: str) -> list[str]:
    """Error-log lines of the gateway (everything that is not a JSON access line)."""
    out = compose("logs", "--no-log-prefix", "--since", since, "gateway").stdout
    return [line.strip() for line in out.splitlines() if line.strip() and not line.strip().startswith("{")]


def gateway_identity() -> tuple[str, str, int]:
    """Container id, start time and restart count: equal values mean nginx was not restarted."""
    g = inspect(containers("gateway")[0]["Name"])
    return g["Id"], g["State"]["StartedAt"], g["RestartCount"]


def log_entry(request_id: str, timeout: float = 10) -> dict:
    return wait_until(lambda: gateway_log().get(request_id), timeout, interval=1,
                      what=f"log line {request_id}")


# --- users ----------------------------------------------------------------------------

@dataclass
class User:
    id: int
    email: str
    headers: dict


def login(email: str, password: str) -> dict:
    with gw() as c:
        response = c.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, f"login failed: {response.status_code}"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def admin() -> User:
    headers = login(ENV["ADMIN_EMAIL"], ENV["ADMIN_PASSWORD"])
    with gw() as c:
        me = c.get("/api/users/me", headers=headers).json()
    return User(me["id"], me["email"], headers)


def new_customer() -> User:
    email = f"gwt-{uuid.uuid4().hex[:12]}@example.com"
    password = secrets.token_urlsafe(18)  # only in memory, never printed
    with gw() as c:
        created = c.post("/api/auth/register", json={"email": email, "full_name": "Cliente Gateway",
                                                     "password": password})
    assert created.status_code == 201, created.text
    return User(created.json()["id"], email, login(email, password))


def new_product(adm: User, stock: int, price: str = "12.50") -> int:
    """A product only for this test, so existing data is never changed."""
    sku = f"GWT-{uuid.uuid4().hex[:10].upper()}"
    with gw() as c:
        product = c.post("/api/products", headers=adm.headers, json={
            "sku": sku, "name": f"Producto gateway {sku}", "category": "pruebas", "price": price})
        assert product.status_code == 201, product.text
        product_id = product.json()["id"]
        adjusted = c.post(f"/api/inventory/{product_id}/adjustments", json={"delta": stock, "reason": "gateway"},
                          headers={**adm.headers, "Idempotency-Key": f"gwt-{uuid.uuid4()}"})
        assert adjusted.status_code == 201, adjusted.text
    return product_id
