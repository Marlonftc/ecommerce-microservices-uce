from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_live_returns_ok():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "user-service"}
