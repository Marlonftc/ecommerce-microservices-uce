"""Checks 10, 11 and 14 from the service side: which headers really arrive.

The real services do not echo request headers, so for this test only a small
echo server takes the place of notification-service: the real container is
stopped and a temporary container joins ecommerce-net with the network alias
"notification-service". The gateway configuration is not changed: it finds the
echo server through Docker DNS, exactly as it finds a new replica.
order-worker is stopped meanwhile, so no real notification is sent to the echo.
"""
import json

import pytest

from support import compose, containers, gw, inspect, rid, run, wait_healthy, wait_until

pytestmark = pytest.mark.docker

ECHO_NAME = "gwt-header-echo"
ECHO_SERVER = """
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class Echo(BaseHTTPRequestHandler):
    def handle_one(self):
        size = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(size).decode()
        out = json.dumps({'echo': True, 'method': self.command, 'path': self.path, 'body': body,
                          'headers': {k.lower(): v for k, v in self.headers.items()}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_one
    def log_message(self, *args):
        pass
HTTPServer(('0.0.0.0', 8000), Echo).serve_forever()
"""


@pytest.fixture(scope="module")
def echo():
    gateway = inspect(containers("gateway")[0]["Name"])
    network = next(n for n in gateway["NetworkSettings"]["Networks"] if n.endswith("_ecommerce-net"))
    image = inspect(containers("notification-service")[0]["Name"])["Config"]["Image"]
    run("docker", "rm", "-f", ECHO_NAME, check=False)
    compose("stop", "order-worker", "notification-service")
    try:
        run("docker", "run", "-d", "--rm", "--name", ECHO_NAME, "--network", network,
            "--network-alias", "notification-service", image, "python", "-c", ECHO_SERVER)
        with gw() as c:
            wait_until(lambda: c.get("/api/notifications").headers.get("content-type", "").startswith(
                "application/json") and c.get("/api/notifications").json().get("echo"), 40, interval=1,
                what="the gateway to reach the echo server")
        yield
    finally:
        run("docker", "rm", "-f", ECHO_NAME, check=False)
        compose("start", "notification-service", "order-worker")
        wait_healthy("notification-service")
        wait_healthy("order-worker", timeout=90)


def test_10_11_14_headers_received_by_the_service(echo):
    request_id = rid("headers")
    with gw() as c:
        r = c.post("/api/notifications/abc/read?x=1&y=%C3%B1", content='{"a": 1}', headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer header.payload.signature",
            "Idempotency-Key": "gwt-idem-123",
            "X-Internal-Key": "sent-by-an-outside-client",
            "X-Forwarded-For": "203.0.113.66",
            "X-Real-IP": "203.0.113.67",
            "X-Request-ID": request_id,
        })
    seen = r.json()
    print("\n" + json.dumps({k: seen["headers"].get(k) for k in (
        "authorization", "idempotency-key", "x-internal-key", "x-request-id", "x-forwarded-for", "x-real-ip")},
        indent=1))
    h = seen["headers"]
    # Method, path (without /api), query and body unchanged
    assert (seen["method"], seen["path"], seen["body"]) == ("POST", "/notifications/abc/read?x=1&y=%C3%B1", '{"a": 1}')
    # 14: passed unchanged
    assert h["authorization"] == "Bearer header.payload.signature"
    assert h["idempotency-key"] == "gwt-idem-123"
    # 11: removed by the gateway
    assert "x-internal-key" not in h
    # 10: the same request id reaches the service and comes back to the client
    assert h["x-request-id"] == request_id == r.headers["x-request-id"]
    # The client's forwarding headers are replaced with the address nginx sees
    assert "203.0.113" not in h["x-forwarded-for"] and "203.0.113" not in h["x-real-ip"]
    assert h["x-forwarded-for"] == h["x-real-ip"]


def test_10_generated_request_id_reaches_the_service(echo):
    with gw() as c:
        r = c.get("/api/notifications", headers={"X-Request-ID": "not valid!"})
    assert r.json()["headers"]["x-request-id"] == r.headers["x-request-id"]
    assert len(r.headers["x-request-id"]) == 32
