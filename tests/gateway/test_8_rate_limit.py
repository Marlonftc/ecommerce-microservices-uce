"""Check 13: login is limited to 10 requests per minute per IP (burst 5, nodelay).

All requests from this PC share one client address, and earlier tests also log
in, so the test first waits 40 s: the limit then starts empty (10/min refills
one request every 6 s; 5 x 6 s = 30 s). Then 12 attempts are sent at once:
1 + burst 5 = 6 reach user-service (401, wrong password) and the rest get 429.
"""
import time
import uuid

from support import gateway_log, gw, rid, wait_until


def test_13_login_rate_limit():
    time.sleep(40)
    ids, statuses = [], []
    with gw() as c:
        for _ in range(12):
            request_id = rid("login")
            r = c.post("/api/auth/login", json={"email": f"gwt-{uuid.uuid4().hex[:8]}@example.com",
                                                "password": "wrong-password"},
                       headers={"X-Request-ID": request_id})
            ids.append(request_id)
            statuses.append(r.status_code)
            if r.status_code == 429:
                limited = r
        print(f"\nstatuses: {statuses}")
        assert statuses[:6] == [401] * 6           # reached user-service
        assert statuses[6:] == [429] * 6           # stopped by the gateway
        assert limited.json() == {"detail": {"code": "TOO_MANY_REQUESTS",
                                             "message": "Too many login attempts, try again later"}}
        assert limited.headers["retry-after"] == "6"
        assert limited.headers["x-request-id"] == ids[-1]

        # Only login is limited: other routes, including register, keep working
        assert all(c.get("/api/products").status_code == 200 for _ in range(15))
        assert c.post("/api/auth/register", json={}).status_code == 422

    log = wait_until(lambda: (lambda g: g if all(i in g for i in ids) else None)(gateway_log()), 15,
                     interval=1, what="log lines")
    assert all(log[i]["upstream_addr"] != "" for i in ids[:6])
    assert all(log[i]["upstream_addr"] == "" and log[i]["status"] == 429 for i in ids[6:])

    # After 6 s one more attempt is allowed again
    time.sleep(6.5)
    with gw() as c:
        r = c.post("/api/auth/login", json={"email": "gwt-again@example.com", "password": "wrong-password"})
    assert r.status_code == 401
