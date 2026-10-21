import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Without a local .env (for example on another machine), use a dummy password.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")

# Fixed internal key for the tests (overrides the value of a local .env)
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
INTERNAL = {"X-Internal-Key": "test-internal-key"}

# Temporary key pair: the private key only exists here to simulate user-service
_keys_dir = Path(tempfile.mkdtemp())
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
(_keys_dir / "public.pem").write_bytes(_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_keys_dir / "public.pem")

# Products that the fake product-service says exist (the initial catalog and 50)
KNOWN_PRODUCTS = set(range(1, 9)) | {50}


def make_token(user_id: int = 1, role: str = "customer") -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "role": role, "iss": "user-service", "aud": "ecommerce-uce",
              "iat": now, "exp": now + timedelta(minutes=30)}
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256")


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_token(user_id=13, role='admin')}"}


@pytest.fixture
def customer_headers():
    return {"Authorization": f"Bearer {make_token(role='customer')}"}


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


@pytest.fixture
def product_service(monkeypatch):
    """Replaces the real call to product-service. Tests can make it fail."""
    from app.clients import product_client

    state = {"down": False, "calls": 0}

    def fake_product_exists(product_id: int) -> bool:
        state["calls"] += 1
        if state["down"]:
            raise product_client.ProductServiceUnavailable("simulated outage")
        return product_id in KNOWN_PRODUCTS

    monkeypatch.setattr(product_client, "product_exists", fake_product_exists)
    return state


@pytest.fixture
def client(db_session, product_service):
    """HTTP client using the rollback session, with the initial stock loaded."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.seed import seed_stock

    seed_stock(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
