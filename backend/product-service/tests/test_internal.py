INTERNAL = {"X-Internal-Key": "test-internal-key"}


def test_internal_endpoint_requires_the_internal_key(client):
    assert client.get("/internal/products/1").status_code == 403
    assert client.get("/internal/products/1", headers={"X-Internal-Key": "wrong"}).status_code == 403


def test_internal_endpoint_returns_the_product(client):
    response = client.get("/internal/products/1", headers=INTERNAL)

    assert response.status_code == 200
    assert response.json()["sku"] == "LAP-001"


def test_internal_endpoint_includes_inactive_products(client, admin_headers):
    client.delete("/products/8", headers=admin_headers)

    response = client.get("/internal/products/8", headers=INTERNAL)

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_internal_endpoint_returns_404_for_unknown_product(client):
    assert client.get("/internal/products/999999", headers=INTERNAL).status_code == 404


def test_admin_token_does_not_open_internal_endpoints(client, admin_headers):
    # A user token, even an admin one, is not the internal key
    assert client.get("/internal/products/1", headers=admin_headers).status_code == 403
