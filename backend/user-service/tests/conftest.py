import os
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Without a local .env (for example on another machine), use a dummy password.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")

# Tests never use the real keys: a temporary pair is generated.
_keys_dir = Path(tempfile.mkdtemp())
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
(_keys_dir / "private.pem").write_bytes(_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
(_keys_dir / "public.pem").write_bytes(_key.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
os.environ["JWT_PRIVATE_KEY_PATH"] = str(_keys_dir / "private.pem")
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_keys_dir / "public.pem")


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
    """HTTP test client that uses the rollback session above."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
