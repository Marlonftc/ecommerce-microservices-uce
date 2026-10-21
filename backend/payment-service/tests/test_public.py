import uuid

from tests.conftest import bearer, pay, payment_body


def test_payments_require_a_token(client):
    assert client.get("/payments").status_code == 401
    assert client.get("/payments", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_customer_sees_only_their_own_payments(client):
    mine = pay(client, payment_body(user_id=7)).json()
    pay(client, payment_body(user_id=8))

    response = client.get("/payments", headers=bearer(7))

    assert [p["id"] for p in response.json()] == [mine["id"]]


def test_customer_cannot_read_another_users_payment(client):
    other = pay(client, payment_body(user_id=8)).json()

    response = client.get(f"/payments/{other['id']}", headers=bearer(7))

    # Same answer as a payment that does not exist: it does not reveal anything
    assert response.status_code == 404


def test_customer_reads_own_payment(client):
    mine = pay(client, payment_body(user_id=7)).json()

    assert client.get(f"/payments/{mine['id']}", headers=bearer(7)).json()["status"] == "APPROVED"


def test_admin_sees_all_payments(client):
    a = pay(client, payment_body(user_id=7)).json()
    b = pay(client, payment_body(user_id=8)).json()

    ids = {p["id"] for p in client.get("/payments", headers=bearer(1, "admin")).json()}

    assert {a["id"], b["id"]} <= ids
    assert client.get(f"/payments/{b['id']}", headers=bearer(1, "admin")).status_code == 200


def test_unknown_payment_returns_404(client):
    assert client.get(f"/payments/{uuid.uuid4()}", headers=bearer(7)).status_code == 404
