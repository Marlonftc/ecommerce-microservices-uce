import pytest

from app import seed


def test_seed_stops_with_invalid_admin_email(monkeypatch):
    monkeypatch.setattr(seed.settings, "admin_email", "admin@ecommerce.local")
    monkeypatch.setattr(seed.settings, "admin_password", "Admin1234")

    with pytest.raises(SystemExit) as exc:
        seed.create_admin()

    assert exc.value.code == 1
