import json
import os
from pathlib import Path

import httpx2
import pytest

# Without a local .env (for example on another machine), use a dummy password.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")

# Fixed internal key for the tests (overrides a local .env)
os.environ["INTERNAL_API_KEY"] = "test-internal-key"


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
