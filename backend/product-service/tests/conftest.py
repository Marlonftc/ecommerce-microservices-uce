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

# Temporary key pair. The private key only exists here, to simulate tokens
# issued by user-service; product-service itself only receives the public key.
_keys_dir = Path(tempfile.mkdtemp())
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
(_keys_dir / "public.pem").write_bytes(_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_keys_dir / "public.pem")


def make_token(user_id: int = 1, role: str = "customer", minutes: int = 30, key=PRIVATE_KEY) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user_id), "role": role, "iss": "user-service", "aud": "ecommerce-uce",
              "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {make_token(role='admin')}"}


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
def client(db_session):
    """HTTP client using the rollback session, with the initial catalog loaded."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.seed import seed_products

    seed_products(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
