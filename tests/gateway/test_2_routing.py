"""Checks 1, 10 and 14: every public route reaches the right service and endpoint.

A status code alone is not enough. Each call is verified twice:
- the body is the one only that endpoint returns (ids, emails, versions), and
- the gateway log line of that request (found by its X-Request-ID) shows an
  upstream address that belongs to the expected service's containers.
"""
import re
import uuid

import pytest

from support import NOT_FOUND, compose, gateway_log, gw, ips, log_entry, new_product, rid, wait_until


class Tracker:
    """Sends requests with a unique X-Request-ID and remembers the expected service."""

    def __init__(self):
        self.expected: dict[str, str] = {}
        self.client = gw()

    def call(self, service: str | None, method: str, path: str, headers: dict | None = None, **kwargs):
        request_id = rid(method.lower())
        response = self.client.request(method, path, headers={**(headers or {}), "X-Request-ID": request_id},
                                       **kwargs)
        assert response.headers["x-request-id"] == request_id
        self.expected[request_id] = service
        return response

    def verify_log(self):
        upstreams = {svc: ips(svc) for svc in set(self.expected.values()) if svc}
        log = wait_until(lambda: (lambda g: g if all(r in g for r in self.expected) else None)(gateway_log()),
                         15, interval=1, what="all log lines")
        for request_id, service in self.expected.items():
            entry = log[request_id]
            addr = entry["upstream_addr"]
            if service is None:
                assert addr == "", (request_id, entry)      # answered by nginx, nothing forwarded
            else:
                ip = addr.split(":")[0]
                assert ip in upstreams[service], (request_id, service, entry)
        return len(self.expected)


@pytest.fixture
def t():
    tracker = Tracker()
    yield tracker
    tracker.client.close()


# 1 ------------------------------------------------------------------------------------------
def test_1_every_public_route_reaches_its_service(t, adm, customer):
    email = f"gwt-{uuid.uuid4().hex[:12]}@example.com"
    # user-service
    r = t.call("user-service", "POST", "/api/auth/register",
               json={"email": email, "full_name": "Ruta Registro", "password": uuid.uuid4().hex})
    assert r.status_code == 201 and r.json()["email"] == email
    r = t.call("user-service", "POST", "/api/auth/login", json={"email": email, "password": "wrong-password"})
    assert r.status_code == 401
    r = t.call("user-service", "GET", "/api/users/me", headers=customer.headers)
    assert r.status_code == 200 and r.json()["email"] == customer.email
    r = t.call("user-service", "GET", "/api/users", params={"limit": 1}, headers=adm.headers)
    assert r.status_code == 200 and len(r.json()) == 1 and "email" in r.json()[0]

    # product-service (query parameters must arrive unchanged)
    sku = f"GWT-{uuid.uuid4().hex[:10].upper()}"
    r = t.call("product-service", "POST", "/api/products", headers=adm.headers,
               json={"sku": sku, "name": "Ruta producto", "category": "pruebas", "price": "9.90"})
    assert r.status_code == 201
    product = r.json()["id"]
    r = t.call("product-service", "GET", "/api/products", params={"ids": f"{product}", "limit": 5})
    assert r.status_code == 200 and [p["id"] for p in r.json()["items"]] == [product] and r.json()["limit"] == 5
    r = t.call("product-service", "GET", f"/api/products/{product}")
    assert r.status_code == 200 and r.json()["sku"] == sku
    r = t.call("product-service", "PUT", f"/api/products/{product}", headers=adm.headers,
               json={"sku": sku, "name": "Ruta producto editado", "category": "pruebas", "price": "9.90"})
    assert r.status_code == 200 and r.json()["name"] == "Ruta producto editado"

    # inventory-service
    key = f"gwt-{uuid.uuid4()}"
    r = t.call("inventory-service", "POST", f"/api/inventory/{product}/adjustments", json={"delta": 4, "reason": "ruta"},
               headers={**adm.headers, "Idempotency-Key": key})
    assert r.status_code == 201 and (r.json()["product_id"], r.json()["delta"]) == (product, 4)
    r = t.call("inventory-service", "GET", f"/api/inventory/{product}", headers=adm.headers)
    assert r.status_code == 200 and (r.json()["available"], r.json()["reserved"]) == (4, 0)
    r = t.call("inventory-service", "GET", f"/api/inventory/{product}/adjustments", headers=adm.headers)
    assert r.status_code == 200 and [a["delta"] for a in r.json()] == [4]
    r = t.call("inventory-service", "GET", "/api/inventory", params={"ids": f"{product}"}, headers=adm.headers)
    assert r.status_code == 200 and [(s["product_id"], s["available"]) for s in r.json()] == [(product, 4)]

    # order-service: cart (PUT and DELETE methods preserved)
    r = t.call("order-service", "PUT", f"/api/cart/items/{product}", json={"quantity": 2}, headers=customer.headers)
    assert r.status_code == 200 and [(i["product_id"], i["quantity"]) for i in r.json()["items"]] == [(product, 2)]
    r = t.call("order-service", "GET", "/api/cart", headers=customer.headers)
    assert r.status_code == 200 and r.json()["items"][0]["sku"] == sku
    r = t.call("order-service", "DELETE", f"/api/cart/items/{product}", headers=customer.headers)
    assert r.status_code == 200 and r.json()["items"] == []
    r = t.call("order-service", "DELETE", "/api/cart", headers=customer.headers)
    assert r.status_code == 200 and r.json()["items"] == []
    r = t.call("order-service", "GET", "/api/orders", params={"size": 3}, headers=customer.headers)
    assert r.status_code == 200 and r.json()["size"] == 3
    r = t.call("order-service", "GET", f"/api/orders/{uuid.uuid4()}", headers=customer.headers)
    assert r.status_code == 404
    r = t.call("order-service", "GET", "/api/admin/operations", params={"size": 2}, headers=adm.headers)
    assert r.status_code == 200 and r.json()["size"] == 2
    r = t.call("order-service", "POST", "/api/admin/operations/999999999/retry", headers=adm.headers)
    assert r.status_code == 404 and r.json()["detail"] == "Operation not found"

    # payment-service and notification-service
    r = t.call("payment-service", "GET", "/api/payments", headers=customer.headers)
    assert r.status_code == 200 and isinstance(r.json(), list)
    r = t.call("payment-service", "GET", f"/api/payments/{uuid.uuid4()}", headers=customer.headers)
    assert r.status_code == 404
    r = t.call("notification-service", "GET", "/api/notifications", params={"size": 4}, headers=customer.headers)
    assert r.status_code == 200 and r.json()["size"] == 4
    r = t.call("notification-service", "PATCH", f"/api/notifications/{uuid.uuid4()}/read", headers=customer.headers)
    assert r.status_code == 404

    # product-service DELETE last: the product is deactivated
    r = t.call("product-service", "DELETE", f"/api/products/{product}", headers=adm.headers)
    assert r.status_code == 204

    assert t.verify_log() == 25


def test_1_unknown_routes_answer_404_without_forwarding(t):
    for path in ["/api/productsXYZ", "/api/products-old", "/api/PRODUCTS", "/API/products", "/api", "/api/",
                 "/", "/api/auth", "/api/admin", "/api/admin/users", "/api/health/ready", "/docs",
                 "/health/ready", "/api/cartx", "/api/ordersXYZ/1"]:
        r = t.call(None, "GET", path)
        assert (r.status_code, r.json()) == (404, NOT_FOUND), path
        assert "x-instance" not in r.headers
    assert t.verify_log() == 15


def test_1_service_redirect_keeps_the_api_prefix(t, customer):
    r = t.call("order-service", "GET", "/api/orders/", headers=customer.headers, follow_redirects=False)
    assert r.status_code == 307
    assert re.fullmatch(r"(http://[^/]+)?/api/orders", r.headers["location"]), r.headers["location"]
    t.verify_log()


def test_1_service_errors_are_passed_unchanged(t, customer):
    # 422 of FastAPI and 409 of order-service keep their own JSON body
    r = t.call("order-service", "PUT", "/api/cart/items/abc", json={"quantity": 1}, headers=customer.headers)
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    r = t.call("order-service", "POST", "/api/orders", json={"cart_version": 0},
               headers={**customer.headers, "Idempotency-Key": f"gwt-{uuid.uuid4()}"})
    assert r.status_code in (409, 422) and "detail" in r.json()
    t.verify_log()


def test_1_body_limit_is_1_mb(t, adm):
    r = t.call(None, "POST", "/api/products", headers={**adm.headers, "Content-Type": "application/json"},
               content=b"x" * (1024 * 1024 + 1))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    t.verify_log()


# 10 -----------------------------------------------------------------------------------------
def test_10_request_id_generated_validated_and_logged():
    with gw() as c:
        generated = c.get("/api/products").headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", generated)
        assert log_entry(generated)["path"] == "/api/products"

        own = rid("own")
        assert c.get("/api/products", headers={"X-Request-ID": own}).headers["x-request-id"] == own

        for bad in ["short", "x" * 65, "has spaces in it", "inject\"quote", "../../etc"]:
            replaced = c.get("/api/products", headers={"X-Request-ID": bad}).headers["x-request-id"]
            assert re.fullmatch(r"[0-9a-f]{32}", replaced), bad


def test_10_log_has_no_tokens_or_passwords(adm):
    password = f"Secret-{uuid.uuid4().hex}"
    request_id = rid("secret")
    with gw() as c:
        # register is not rate limited; the invalid email makes it fail with 422
        c.post("/api/auth/register", json={"email": "not-an-email", "full_name": "Log", "password": password},
               headers={"X-Request-ID": request_id})
        c.get("/api/users/me", params={"token": "not-logged"}, headers=adm.headers)
    entry = log_entry(request_id)
    assert set(entry) == {"time", "request_id", "client", "method", "path", "status", "bytes", "request_time",
                          "upstream_addr", "upstream_status", "upstream_connect_time", "upstream_response_time"}
    log = compose("logs", "--no-log-prefix", "--since", "5m", "gateway").stdout
    token = adm.headers["Authorization"].split()[1]
    assert password not in log and token not in log and "not-logged" not in log


# 14 -----------------------------------------------------------------------------------------
def test_14_authorization_and_idempotency_key_reach_the_services(adm, customer):
    with gw() as c:
        assert c.get("/api/users/me").status_code == 401
        assert c.get("/api/users/me", headers=customer.headers).json()["id"] == customer.id

        product = new_product(adm, stock=1)
        key = f"gwt-{uuid.uuid4()}"
        headers = {**adm.headers, "Idempotency-Key": key}
        first = c.post(f"/api/inventory/{product}/adjustments", json={"delta": 7, "reason": "idem"}, headers=headers)
        second = c.post(f"/api/inventory/{product}/adjustments", json={"delta": 7, "reason": "idem"}, headers=headers)
        assert (first.status_code, second.status_code) == (201, 200)
        assert first.json()["id"] == second.json()["id"]
        assert c.get(f"/api/inventory/{product}", headers=adm.headers).json()["available"] == 8   # 1 + 7 once
