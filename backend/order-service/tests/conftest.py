import json
import os
import uuid
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
    """Answers the internal reservation endpoints like inventory-service.

    mode (reserve): "ok", "down", "timeout", "insufficient", "released".
    confirm_mode / release_mode: "ok" or "down".
    on_reserve: optional function called before answering (to simulate a race).
    """

    def __init__(self):
        self.mode = "ok"
        self.confirm_mode = "ok"
        self.release_mode = "ok"
        self.reserved: dict[str, list] = {}
        self.status: dict[str, str] = {}
        self.calls = 0
        self.reserve_calls = 0
        self.on_reserve = None

    @staticmethod
    def _answer(code, order_id, status, items):
        return httpx2.Response(code, json={"order_id": order_id, "status": status, "items": items})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        path = request.url.path
        if path.endswith("/confirm") or path.endswith("/release"):
            order_id = path.split("/")[-2]
            action = path.rsplit("/", 1)[1]
            if (self.confirm_mode if action == "confirm" else self.release_mode) == "down":
                raise httpx2.ConnectError("inventory-service down")
            current = self.status.get(order_id)
            if action == "confirm":
                if current is None:
                    return httpx2.Response(404, json={"detail": "Reservation not found"})
                if current == "RELEASED":
                    return httpx2.Response(409, json={"detail": {"code": "INVALID_TRANSITION", "message": "x"}})
                self.status[order_id] = "CONFIRMED"
            else:
                if current == "CONFIRMED":
                    return httpx2.Response(409, json={"detail": {"code": "INVALID_TRANSITION", "message": "x"}})
                self.status[order_id] = "RELEASED"  # unknown order: tombstone
            return self._answer(200, order_id, self.status[order_id], self.reserved.get(order_id, []))

        self.reserve_calls += 1
        body = json.loads(request.content)
        if self.on_reserve:
            self.on_reserve(body)
        if self.mode == "down":
            raise httpx2.ConnectError("inventory-service down")
        if self.status.get(body["order_id"]) == "RELEASED" or self.mode == "released":
            return httpx2.Response(409, json={"detail": {"code": "RESERVATION_RELEASED", "message": "x"}})
        if self.mode == "timeout":
            self.reserved[body["order_id"]] = body["items"]
            self.status[body["order_id"]] = "RESERVED"
            raise httpx2.ReadTimeout("no answer")
        if self.mode == "insufficient":
            return httpx2.Response(409, json={"detail": {"code": "INSUFFICIENT_STOCK", "message": "x",
                                                         "product_id": body["items"][0]["product_id"]}})
        created = body["order_id"] not in self.reserved
        self.reserved[body["order_id"]] = body["items"]
        self.status[body["order_id"]] = "RESERVED"
        return self._answer(201 if created else 200, body["order_id"], "RESERVED", body["items"])


class FakePayments:
    """Answers POST /internal/payments like payment-service: one payment per
    order, the stored result for a repeated key. mode: "ok", "down", "slow"
    (stored APPROVED but the answer is lost), "conflict"."""

    def __init__(self):
        self.mode = "ok"
        self.payments: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.on_call = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if self.on_call:
            self.on_call()
        body = json.loads(request.content)
        self.requests.append({"body": body, "key": request.headers["Idempotency-Key"]})
        if self.mode == "down":
            raise httpx2.ConnectError("payment-service down")
        if self.mode == "conflict":
            return httpx2.Response(409, json={"detail": {"code": "IDEMPOTENCY_KEY_REUSED", "message": "x"}})
        order_id = body["order_id"]
        created = order_id not in self.payments
        if created:
            status = "REJECTED" if body["simulation"] == "REJECT" else "APPROVED"
            self.payments[order_id] = {"id": str(uuid.uuid4()), "order_id": order_id, "status": status,
                                       "amount": body["amount"], "currency": body["currency"],
                                       "simulation": body["simulation"]}
            if body["simulation"] == "SLOW_APPROVE" or self.mode == "slow":
                # APPROVED is stored first; the answer arrives after the client gave up
                raise httpx2.ReadTimeout("payment-service is slow")
        return httpx2.Response(201 if created else 200, json=self.payments[order_id])


class FakeNotifications:
    """Answers POST /internal/notifications. mode: "ok" or "down"."""

    def __init__(self):
        self.mode = "ok"
        self.events: dict[str, dict] = {}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if self.mode == "down":
            raise httpx2.ConnectError("notification-service down")
        body = json.loads(request.content)
        created = body["event_id"] not in self.events
        self.events.setdefault(body["event_id"], body)
        return httpx2.Response(201 if created else 200, json={"id": str(uuid.uuid4())})


@pytest.fixture
def inventory(monkeypatch):
    from app.clients import inventory_client

    fake = FakeInventory()
    monkeypatch.setattr(inventory_client.client, "transport", httpx2.MockTransport(fake))
    return fake


@pytest.fixture
def payments(monkeypatch):
    from app.clients import payment_client

    fake = FakePayments()
    monkeypatch.setattr(payment_client.client, "transport", httpx2.MockTransport(fake))
    return fake


@pytest.fixture
def notifications(monkeypatch):
    from app.clients import notification_client

    fake = FakeNotifications()
    monkeypatch.setattr(notification_client.client, "transport", httpx2.MockTransport(fake))
    return fake


@pytest.fixture
def client(db_session, catalog, inventory, payments, notifications):
    """HTTP client using the rollback session and the fake catalogue."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
