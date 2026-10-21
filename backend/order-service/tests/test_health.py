from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.main import app

client = TestClient(app)


def test_health_live_returns_ok():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "order-service"}


def test_health_ready_returns_503_when_database_is_down(monkeypatch):
    def broken_database():
        raise ConnectionError("database down")

    monkeypatch.setattr(main, "check_database", broken_database)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "down"


def test_configuration_errors_do_not_show_passwords(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)

    try:
        Settings(_env_file=None, db_password="secreto123")
    except Exception as exc:
        assert "internal_api_key" in str(exc)
        assert "secreto123" not in str(exc)
    else:
        raise AssertionError("missing INTERNAL_API_KEY must be an error")


def test_default_settings_match_the_compose_services(monkeypatch):
    s = Settings(_env_file=None, db_password="x", internal_api_key="k")

    assert (s.db_name, s.db_user) == ("orders_db", "order_svc")
    assert s.product_service_url == "http://product-service:8000"
    assert s.inventory_service_url == "http://inventory-service:8000"
    assert s.payment_service_url == "http://payment-service:8000"
    assert s.notification_service_url == "http://notification-service:8000"
    assert (s.http_connect_timeout_seconds, s.http_read_timeout_seconds) == (2, 5)
    assert (s.order_reservation_ttl_minutes, s.reserving_sweep_minutes) == (15, 2)
