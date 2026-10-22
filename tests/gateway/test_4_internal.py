"""Checks 3 and 11: /internal is never reachable from outside.

For every variant the gateway answers 404 itself, and its log line (found by
X-Request-ID) has an empty upstream address: the request reached no service.
"""
import http.client

from support import ENV, GATEWAY, NOT_FOUND, compose, gateway_log, rid, wait_until

VARIANTS = [
    "/internal/products/1",
    "/api/internal/products/1",
    "/INTERNAL/products/1",
    "/Internal/products/1",
    "/api/products/../internal/products/1",
    "/api/products/../../internal/products/1",
    "/api/products/%2e%2e/%2e%2e/internal/products/1",
    "/api/products/%2E%2E/%2E%2E/internal/products/1",
    "/api/products/..%2f..%2finternal/products/1",
    "/api/products/..%2F..%2Finternal/products/1",
    "/api/products/%252e%252e/%252e%252e/internal/products/1",
    "/api/products/%69nternal/products/1",
    "/api/products/%49NTERNAL/products/1",
    "/api/products/%2569nternal/products/1",
    "/api/products//../internal/products/1",
    "/api/products/./../../internal/products/1",
    "//internal/products/1",
    "/api/products/%5c..%5cinternal/products/1",
    "/api/products/;/../../internal/products/1",
    "/api/inventory/../../internal/reservations",
    "/api/orders/x/../../internal/payments",
    "/api/notifications/..;/internal/notifications",
    "/api/products/1/..",
]


def raw_get(path: str, headers: dict) -> tuple[int, dict, bytes]:
    """http.client sends the path exactly as written (no client-side normalisation)."""
    host, port = GATEWAY.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("GET", path)
    for name, value in headers.items():
        conn.putheader(name, value)
    conn.endheaders()
    response = conn.getresponse()
    result = response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    conn.close()
    return result


# 3 + 11 -------------------------------------------------------------------------------------
def test_3_internal_and_evasion_variants_never_reach_a_service():
    sent = {}
    for path in VARIANTS:
        request_id = rid("evasion")
        # Even with the real internal key, sent by an outside client
        status, headers, body = raw_get(path, {"X-Request-ID": request_id,
                                               "X-Internal-Key": ENV.get("INTERNAL_API_KEY") or "x"})
        assert status == 404, (path, status)
        assert body == b'{"detail":{"code":"NOT_FOUND","message":"Route not found"}}', path
        assert "x-instance" not in headers, path      # product-service would have added it
        sent[request_id] = path

    log = wait_until(lambda: (lambda g: g if all(r in g for r in sent) else None)(gateway_log()), 15,
                     interval=1, what="log lines")
    for request_id, path in sent.items():
        assert log[request_id]["status"] == 404, path
        assert log[request_id]["upstream_addr"] == "", (path, log[request_id])


def test_3_second_barrier_inside_the_network():
    """Defence in depth: inside ecommerce-net, /internal still requires X-Internal-Key.
    The call is made from order-service, without the key."""
    script = ("import urllib.request, urllib.error\n"
              "try:\n"
              "    urllib.request.urlopen('http://product-service:8000/internal/products/1', timeout=5)\n"
              "    print('STATUS 200')\n"
              "except urllib.error.HTTPError as e:\n"
              "    print('STATUS', e.code)\n")
    result = compose("exec", "-T", "order-service", "python", "-c", script)
    assert result.stdout.strip() in ("STATUS 401", "STATUS 403"), result.stdout


def test_3_not_found_body_is_the_same_for_internal_and_unknown_routes():
    # An outside client cannot tell an internal route from a route that does not exist
    a = raw_get("/internal/products/1", {})
    b = raw_get("/api/does-not-exist", {})
    assert (a[0], a[2]) == (b[0], b[2]) == (404, b'{"detail":{"code":"NOT_FOUND","message":"Route not found"}}')
    assert NOT_FOUND["detail"]["code"] == "NOT_FOUND"
