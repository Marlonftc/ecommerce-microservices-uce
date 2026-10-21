import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Without a local .env (for example on another machine), use a dummy password.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")

# Fixed values for the tests (override a local .env)
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
os.environ["PAYMENT_SIMULATION_ENABLED"] = "true"
INTERNAL = {"X-Internal-Key": "test-internal-key"}

# Temporary key pair: the private key only exists here to simulate user-service
_keys_dir = Path(tempfile.mkdtemp())
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
(_keys_dir / "public.pem").write_bytes(_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_keys_dir / "public.pem")


def make_token(user_id: int, role: str = "customer") -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "role": role, "iss": "user-service", "aud": "ecommerce-uce",
              "iat": now, "exp": now + timedelta(minutes=30)}
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256")


def bearer(user_id: int, role: str = "customer") -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, role)}"}


def payment_body(**changes) -> dict:
    body = {"order_id": str(uuid.uuid4()), "user_id": 7, "amount": "149.90",
            "currency": "USD", "simulation": "APPROVE"}
    body.update(changes)
    return body


def pay(client, body=None, key=None, headers=INTERNAL):
    body = body or payment_body()
    key = key or f"pay-{uuid.uuid4()}"
    return client.post("/internal/payments", json=body, headers={**headers, "Idempotency-Key": key})


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
def client(db_session):
    """HTTP client using the rollback session."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
