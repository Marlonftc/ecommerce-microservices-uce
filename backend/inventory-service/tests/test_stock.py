from sqlalchemy import func, select

from app.models import Stock, StockAdjustment


def adjust(client, headers, product_id=1, delta=10, key="k-1", reason="compra a proveedor"):
    return client.post(f"/inventory/{product_id}/adjustments",
                       json={"delta": delta, "reason": reason},
                       headers={**headers, "Idempotency-Key": key})


def stock(db_session, product_id=1):
    db_session.expire_all()
    s = db_session.get(Stock, product_id)
    return (s.available, s.reserved) if s else None


# --- visibility -------------------------------------------------------------

def test_public_sees_only_available_units(client):
    body = client.get("/inventory/1").json()

    assert body == {"product_id": 1, "available": 15}


def test_admin_also_sees_reserved_units(client, admin_headers):
    body = client.get("/inventory/1", headers=admin_headers).json()

    assert body["available"] == 15 and body["reserved"] == 0


def test_several_products_in_one_request(client):
    body = client.get("/inventory", params={"ids": "1,3,999"}).json()

    assert [s["product_id"] for s in body] == [1, 3]


def test_product_without_stock_returns_404(client):
    assert client.get("/inventory/999").status_code == 404


# --- relative and atomic adjustments ----------------------------------------

def test_only_admin_can_adjust(client, customer_headers):
    assert adjust(client, {}).status_code == 401
    assert adjust(client, customer_headers).status_code == 403


def test_positive_adjustment_adds_units_without_touching_reserved(client, admin_headers, db_session):
    db_session.execute(Stock.__table__.update().where(Stock.product_id == 1).values(reserved=4))
    db_session.commit()

    response = adjust(client, admin_headers, delta=10)

    assert response.status_code == 201
    assert stock(db_session) == (25, 4)


def test_negative_adjustment_removes_units(client, admin_headers, db_session):
    adjust(client, admin_headers, delta=-3)

    assert stock(db_session) == (12, 0)


def test_adjustment_cannot_leave_negative_units(client, admin_headers, db_session):
    response = adjust(client, admin_headers, delta=-16)

    assert response.status_code == 409
    assert stock(db_session) == (15, 0)
    assert db_session.scalar(select(func.count()).where(StockAdjustment.idempotency_key == "k-1")) == 0


def test_zero_adjustment_is_rejected(client, admin_headers):
    assert adjust(client, admin_headers, delta=0).status_code == 422


def test_idempotency_key_is_required(client, admin_headers):
    response = client.post("/inventory/1/adjustments", json={"delta": 5}, headers=admin_headers)

    assert response.status_code == 422


# --- idempotency ------------------------------------------------------------

def test_retry_with_same_key_applies_only_once(client, admin_headers, db_session):
    first = adjust(client, admin_headers, delta=10, key="retry-1")
    second = adjust(client, admin_headers, delta=10, key="retry-1")

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert stock(db_session) == (25, 0)


def test_same_key_with_different_data_returns_409(client, admin_headers, db_session):
    adjust(client, admin_headers, delta=10, key="retry-2")

    response = adjust(client, admin_headers, delta=20, key="retry-2")

    assert response.status_code == 409
    assert stock(db_session) == (25, 0)


def test_retry_does_not_call_product_service_again(client, admin_headers, product_service):
    adjust(client, admin_headers, key="retry-3")
    adjust(client, admin_headers, key="retry-3")

    assert product_service["calls"] == 1


# --- validation with product-service ----------------------------------------

def test_unknown_product_returns_404_and_creates_no_stock(client, admin_headers, db_session):
    response = adjust(client, admin_headers, product_id=777)

    assert response.status_code == 404
    assert stock(db_session, 777) is None


def test_new_valid_product_gets_its_stock_row(client, admin_headers, db_session):
    response = adjust(client, admin_headers, product_id=50, delta=12)

    assert response.status_code == 201
    assert stock(db_session, 50) == (12, 0)


def test_product_service_down_returns_503_and_changes_nothing(client, admin_headers, db_session, product_service):
    product_service["down"] = True

    response = adjust(client, admin_headers, delta=10)

    assert response.status_code == 503
    assert stock(db_session) == (15, 0)


# --- history ----------------------------------------------------------------

def test_adjustment_is_recorded_in_history(client, admin_headers):
    adjust(client, admin_headers, delta=10, key="h-1", reason="compra a proveedor")
    adjust(client, admin_headers, delta=-2, key="h-2", reason="unidad dañada")

    history = client.get("/inventory/1/adjustments", headers=admin_headers).json()

    latest, previous = history[0], history[1]
    assert (latest["delta"], latest["available_before"], latest["available_after"]) == (-2, 25, 23)
    assert latest["admin_user_id"] == 13 and latest["reason"] == "unidad dañada"
    assert (previous["delta"], previous["available_before"], previous["available_after"]) == (10, 15, 25)
    assert history[-1]["reason"] == "initial stock"


def test_history_is_admin_only(client, customer_headers):
    assert client.get("/inventory/1/adjustments", headers=customer_headers).status_code == 403
