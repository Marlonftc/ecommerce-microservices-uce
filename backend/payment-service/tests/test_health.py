from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.main import app

client = TestClient(app)


def test_health_live_returns_ok():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "payment-service"}


def test_health_ready_returns_503_when_database_is_down(monkeypatch):
    def broken_database():
        raise ConnectionError("database down")

    monkeypatch.setattr(main, "check_database", broken_database)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "down"


def test_simulation_is_disabled_by_default(monkeypatch):
    # Without the variable, payments must not be simulated
    monkeypatch.delenv("PAYMENT_SIMULATION_ENABLED", raising=False)

    settings = Settings(_env_file=None, db_password="x", internal_api_key="k")

    assert settings.payment_simulation_enabled is False


def test_configuration_errors_do_not_show_passwords(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)

    try:
        Settings(_env_file=None, db_password="secreto123")
    except Exception as exc:
        assert "internal_api_key" in str(exc)
        assert "secreto123" not in str(exc)
    else:
        raise AssertionError("missing INTERNAL_API_KEY must be an error")
