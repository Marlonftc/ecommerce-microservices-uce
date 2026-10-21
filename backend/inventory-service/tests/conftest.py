import os
from pathlib import Path

import pytest

# Without a local .env (for example on another machine), use dummy values.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")
    os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")


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
