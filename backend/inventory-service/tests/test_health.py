from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


def test_health_live_returns_ok():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "inventory-service"}


def test_health_ready_returns_503_when_database_is_down(monkeypatch):
    def broken_database():
        raise ConnectionError("database down")

    monkeypatch.setattr(main, "check_database", broken_database)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "down"
