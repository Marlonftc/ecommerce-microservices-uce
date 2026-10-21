import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Without a local .env (for example on another machine), use a dummy password.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")

# Fixed internal key for the tests (overrides a local .env)
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

# Temporary key pair: the private key only exists here to simulate user-service
_keys_dir = Path(tempfile.mkdtemp())
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
(_keys_dir / "public.pem").write_bytes(_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_keys_dir / "public.pem")


def make_token(user_id: int, role: str = "customer", minutes: int = 30) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "role": role, "iss": "user-service", "aud": "ecommerce-uce",
              "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256")


def bearer(user_id: int, role: str = "customer") -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, role)}"}


class FakeCatalog:
    """Answers GET /products?ids=... like product-service: only active products."""

    def __init__(self, prices: dict[int, str]):
        self.prices = dict(prices)
        self.down = False
        self.calls = 0
        self.on_call = None  # optional function run before answering (to simulate a race)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        if self.on_call:
            self.on_call()
        if self.down:
            raise httpx2.ConnectError("product-service down")
        ids = [int(i) for i in request.url.params["ids"].split(",")]
        items = [{"id": i, "sku": f"SKU-{i}", "name": f"Producto {i}", "description": "",
                  "category": "test", "price": self.prices[i], "is_active": True,
                  "created_at": "2026-10-21T10:00:00Z", "updated_at": "2026-10-21T10:00:00Z"}
                 for i in ids if i in self.prices]
        return httpx2.Response(200, json={"items": items, "total": len(items), "limit": 100, "offset": 0})


@pytest.fixture
def catalog(monkeypatch):
    """Products 1..60 active, with prices; tests can remove one or set down=True."""
    from app.clients import product_client

    fake = FakeCatalog({i: f"{i}.99" for i in range(1, 61)} | {1: "749.99", 2: "0.10"})
    monkeypatch.setattr(product_client.client, "transport", httpx2.MockTransport(fake))
    return fake


class FakeService:
    """Answers HTTP calls with prepared responses and records every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx2.Response(status, json=body)

    @property
    def last(self) -> httpx2.Request:
        return self.requests[-1]

    def last_json(self):
        return json.loads(self.last.content)


@pytest.fixture
def fake(monkeypatch):
    """fake(module, *answers): replaces the transport of a client module."""
    def install(module, *answers):
        service = FakeService(*answers)
        monkeypatch.setattr(module.client, "transport", httpx2.MockTransport(service))
        return service
    return install


@pytest.fixture
def db_session():
    """A real database session whose changes are rolled back after each test."""
    from sqlalchemy.orm import Session

    from app.database import engine

    try:
        connection = engine.connect()
    except Exception:
        pytest.skip("database not available")

    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


class FakeInventory:
    """Answers POST /internal/reservations like inventory-service.

    mode: "ok", "down", "timeout", "insufficient" (product 1), "released".
    on_reserve: optional function called before answering (to simulate a race).
    """

    def __init__(self):
        self.mode = "ok"
        self.reserved: dict[str, list] = {}
        self.calls = 0
        self.on_reserve = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        body = json.loads(request.content) if request.content else {}
        if self.on_reserve:
            self.on_reserve(body)
        if self.mode == "down":
            raise httpx2.ConnectError("inventory-service down")
        if self.mode == "timeout":
            # inventory reserved it, but the answer was lost
            self.reserved[body["order_id"]] = body["items"]
            raise httpx2.ReadTimeout("no answer")
        if self.mode == "insufficient":
            return httpx2.Response(409, json={"detail": {"code": "INSUFFICIENT_STOCK", "message": "x",
                                                         "product_id": body["items"][0]["product_id"]}})
        if self.mode == "released":
            return httpx2.Response(409, json={"detail": {"code": "RESERVATION_RELEASED", "message": "x"}})
        created = body["order_id"] not in self.reserved
        self.reserved[body["order_id"]] = body["items"]
        return httpx2.Response(201 if created else 200,
                               json={"order_id": body["order_id"], "status": "RESERVED", "items": body["items"]})


@pytest.fixture
def inventory(monkeypatch):
    from app.clients import inventory_client

    fake = FakeInventory()
    monkeypatch.setattr(inventory_client.client, "transport", httpx2.MockTransport(fake))
    return fake


@pytest.fixture
def client(db_session, catalog, inventory):
    """HTTP client using the rollback session and the fake catalogue."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
