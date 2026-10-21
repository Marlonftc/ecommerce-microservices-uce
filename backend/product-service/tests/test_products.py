from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import make_token

NEW_PRODUCT = {"sku": " lap-003 ", "name": "Laptop 13 pulgadas", "description": "Ultraligera",
               "category": " Laptops ", "price": "1099.90"}


def create(client, headers, **changes):
    return client.post("/products", json={**NEW_PRODUCT, **changes}, headers=headers)


# --- public catalog ---------------------------------------------------------

def test_catalog_is_public_and_lists_active_products(client):
    response = client.get("/products")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 8
    assert body["items"][0]["sku"] == "LAP-001"


def test_prices_are_sent_as_exact_strings(client):
    product = client.get("/products/5").json()

    assert product["price"] == "19.50"


def test_filter_by_category_is_normalized(client):
    body = client.get("/products", params={"category": " Accesorios "}).json()

    assert {p["sku"] for p in body["items"]} == {"TEC-001", "MOU-001"}


def test_search_by_name_ignores_case(client):
    body = client.get("/products", params={"q": "LAPTOP"}).json()

    assert {p["sku"] for p in body["items"]} == {"LAP-001", "LAP-002"}


def test_search_treats_percent_as_a_normal_character(client):
    assert client.get("/products", params={"q": "%"}).json()["total"] == 0


def test_pagination_keeps_the_total(client):
    body = client.get("/products", params={"limit": 3, "offset": 3}).json()

    assert len(body["items"]) == 3
    assert body["items"][0]["id"] == 4
    assert body["total"] >= 8


def test_several_products_in_one_request(client):
    body = client.get("/products", params={"ids": "2,5,8,999"}).json()

    assert [p["id"] for p in body["items"]] == [2, 5, 8]  # 999 does not exist


def test_invalid_ids_return_422(client):
    assert client.get("/products", params={"ids": "1,abc"}).status_code == 422


def test_unknown_product_returns_404(client):
    assert client.get("/products/999999").status_code == 404


# --- admin operations -------------------------------------------------------

def test_create_requires_admin(client, customer_headers):
    assert create(client, {}).status_code == 401
    assert create(client, customer_headers).status_code == 403


def test_admin_creates_product_with_normalized_data(client, admin_headers):
    response = create(client, admin_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["sku"] == "LAP-003"
    assert body["category"] == "laptops"
    assert body["price"] == "1099.90"
    assert body["id"] > 8


def test_duplicate_sku_returns_409(client, admin_headers):
    assert create(client, admin_headers, sku="lap-001").status_code == 409


def test_invalid_prices_are_rejected(client, admin_headers):
    assert create(client, admin_headers, price="0").status_code == 422
    assert create(client, admin_headers, price="-5").status_code == 422
    assert create(client, admin_headers, price="10.999").status_code == 422


def test_unknown_fields_are_rejected(client, admin_headers):
    assert create(client, admin_headers, stock=10).status_code == 422


def test_admin_updates_product(client, admin_headers):
    product = client.get("/products/3").json()
    data = {k: product[k] for k in ("sku", "name", "description", "category")} | {"price": "149.90"}

    response = client.put("/products/3", json=data, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["price"] == "149.90"


def test_deactivated_product_disappears_from_public_catalog(client, admin_headers):
    assert client.delete("/products/8", headers=admin_headers).status_code == 204

    assert client.get("/products/8").status_code == 404
    assert 8 not in [p["id"] for p in client.get("/products").json()["items"]]
    # The admin still sees it
    assert client.get("/products/8", headers=admin_headers).json()["is_active"] is False
    inactive = client.get("/products", params={"include_inactive": "true"}, headers=admin_headers)
    assert 8 in [p["id"] for p in inactive.json()["items"]]


def test_customer_cannot_list_inactive_products(client, customer_headers):
    response = client.get("/products", params={"include_inactive": "true"}, headers=customer_headers)

    assert response.status_code == 403


def test_admin_can_reactivate_product(client, admin_headers):
    client.delete("/products/8", headers=admin_headers)
    product = client.get("/products/8", headers=admin_headers).json()
    data = {k: product[k] for k in ("sku", "name", "description", "category", "price")} | {"is_active": True}

    client.put("/products/8", json=data, headers=admin_headers)

    assert client.get("/products/8").status_code == 200


# --- tokens -----------------------------------------------------------------

def test_expired_token_is_rejected(client):
    headers = {"Authorization": f"Bearer {make_token(role='admin', minutes=-1)}"}

    assert create(client, headers).status_code == 401


def test_token_signed_with_another_key_is_rejected(client):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    headers = {"Authorization": f"Bearer {make_token(role='admin', key=other)}"}

    assert create(client, headers).status_code == 401


def test_invalid_token_on_public_endpoint_is_rejected(client):
    # Sending a broken token is an error even where no token is required
    assert client.get("/products", headers={"Authorization": "Bearer abc"}).status_code == 401
