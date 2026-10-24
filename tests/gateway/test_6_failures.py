"""Checks 8 and 9: failures of notification-service seen through the gateway.

Expected classification (default nginx behaviour, all bodies in JSON):
- connection refused                -> 502 UPSTREAM_UNAVAILABLE
- no address left for the service   -> 502 UPSTREAM_UNAVAILABLE ("no live upstreams")
- connection timeout                -> 504 UPSTREAM_TIMEOUT
- the service does not answer       -> 504 UPSTREAM_TIMEOUT (read timeout, 30 s)

Every test restores notification-service in "finally" and waits until it is
healthy and answers through the gateway again, so a failure cannot affect the
next test. nginx is never restarted or reloaded.
"""
import re
import time

import pytest

from support import (compose, containers, gateway_errors, gateway_identity, gateway_log, gw, inspect, ips,
                     log_entry, rid, run, utc_now, wait_healthy, wait_until)

pytestmark = pytest.mark.docker

UNAVAILABLE = {"detail": {"code": "UPSTREAM_UNAVAILABLE", "message": "The service is not available"}}
TIMEOUT = {"detail": {"code": "UPSTREAM_TIMEOUT", "message": "The service did not respond in time"}}
GROUP = "notification_backend"      # logged as upstream_addr when no address is left


def get_notifications(c, headers: dict, label: str):
    request_id = rid(label)
    response = c.get("/api/notifications", headers={**headers, "X-Request-ID": request_id})
    return request_id, response


def restore_notification_service(customer) -> str:
    """Start the real service again and wait until the gateway routes to it."""
    compose("unpause", "notification-service", check=False)
    compose("start", "notification-service")
    wait_healthy("notification-service")
    with gw() as c:
        request_id, _ = wait_until(lambda: (lambda x: x if x[1].status_code == 200 else None)(
            get_notifications(c, customer.headers, "back")), 40, interval=1,
            what="notification-service back through the gateway")
    return request_id


def daemon_time(stamp: str) -> tuple[str, int]:
    """Timestamp written by "docker compose logs --timestamps" (RFC 3339 with up to 9 decimals,
    trailing zeros removed) as a comparable (seconds, nanoseconds) pair."""
    whole, _, fraction = stamp.rstrip("Z").partition(".")
    return whole, int((fraction or "0").ljust(9, "0")[:9])


def gateway_errors_after(request_id: str, since: str) -> list[str]:
    """Error-log lines of the gateway written AFTER the access-log line of request_id.

    Both kinds of line are ordered by the timestamps Docker itself gives them (nanoseconds,
    same clock), not by the clock of this PC and not rounded to whole seconds. support.utc_now()
    starts 1 s earlier and truncates to the second, so a window opened with it can contain up
    to ~2 s of lines written BEFORE the moment it was taken."""
    out = compose("logs", "--no-log-prefix", "--timestamps", "--since", since, "gateway").stdout
    lines = []
    for raw in out.splitlines():
        stamp, _, text = raw.strip().partition(" ")
        if stamp and text.strip():
            lines.append((daemon_time(stamp), text.strip()))
    anchor = next(t for t, text in lines if text.startswith("{") and f'"request_id":"{request_id}"' in text)
    return [text for t, text in lines if t > anchor and not text.startswith("{")]


def dns_lookup_inside_gateway() -> tuple[str, float | None, float]:
    """nslookup from the running gateway container. Returns the output, the time
    measured inside the container (ms, None if unavailable) and the time of the
    whole docker compose exec call (ms), for comparison."""
    # BusyBox "date" has no %N, so the clock is /proc/uptime (10 ms resolution).
    # Only the A record is asked, like nginx with ipv6=off.
    script = ("s=$(cut -d' ' -f1 /proc/uptime); "
              "out=$(nslookup -type=a -timeout=10 -retry=1 notification-service 127.0.0.11 2>&1); "
              "e=$(cut -d' ' -f1 /proc/uptime); echo \"$out\"; echo \"UPTIME_START=$s UPTIME_END=$e\"")
    start = time.monotonic()
    result = compose("exec", "-T", "gateway", "sh", "-c", script, check=False)
    total_ms = (time.monotonic() - start) * 1000
    match = re.search(r"UPTIME_START=([\d.]+) UPTIME_END=([\d.]+)", result.stdout)
    inside_ms = (float(match.group(2)) - float(match.group(1))) * 1000 if match else None
    return result.stdout + result.stderr, inside_ms, total_ms


# 8 ------------------------------------------------------------------------------------------
def test_8_stopped_service_dns_update_and_recovery(customer):
    before = gateway_identity()
    # 1. Available at the start, through its current address
    old_ips = set(ips("notification-service"))
    assert len(old_ips) == 1
    old_ip = next(iter(old_ips))
    with gw() as c:
        # The previous test may have just restarted the service: wait for the gateway to follow it
        request_id, r = wait_until(lambda: (lambda x: x if x[1].status_code == 200 else None)(
            get_notifications(c, customer.headers, "initial")), 40, interval=1, what="initial 200")
    assert log_entry(request_id)["upstream_addr"] == f"{old_ip}:8000"

    since = utc_now()
    try:
        # 2. Stop the container
        compose("stop", "notification-service")

        # 3. Docker DNS answers NXDOMAIN, measured inside the running gateway
        output, inside_ms, total_ms = dns_lookup_inside_gateway()
        inside = f"{inside_ms:.0f} ms" if inside_ms is not None else "not available"
        print(f"\nDNS after stop: inside the container {inside}, whole docker compose exec {total_ms:.0f} ms")
        print("\n".join(line for line in output.splitlines() if line.strip()))
        assert "NXDOMAIN" in output
        assert inside_ms is None or inside_ms < 5000          # below resolver_timeout

        # 4 + 5. JSON answers until nginx re-resolves the name and drops the old address
        seen = []
        with gw() as c:
            def updated():
                request_id, r = get_notifications(c, customer.headers, "stopped")
                assert r.headers["content-type"].startswith("application/json")
                assert r.status_code in (502, 504), r.status_code
                assert r.json() == (UNAVAILABLE if r.status_code == 502 else TIMEOUT)
                entry = log_entry(request_id)
                seen.append((r.status_code, entry["upstream_addr"], entry["upstream_connect_time"]))
                return request_id if entry["upstream_addr"] == GROUP else None

            # First request answered without the old address: the update is complete from here on
            first_updated = wait_until(updated, 40, interval=0.5, what="nginx to drop the old address")
        print("during the change (status, upstream_addr, connect_time):")
        for row in seen:
            print("  ", row)
        for status, addr, connect in seen:
            if addr != GROUP:
                # Before the update: only the old address, refused (502) or connect timeout (504)
                assert addr == f"{old_ip}:8000"
                assert (status, connect) in ((502, "-"), (504, "-"))

        errors = gateway_errors(since)
        assert any("notification-service could not be resolved (3: Host not found)" in e for e in errors), errors
        assert any("no live upstreams" in e for e in errors)

        # 6. After the update: 15 s (more than valid=10s) without a single connection to the old address.
        # The window starts at the log line of the first updated request (see gateway_errors_after):
        # a request sent before the update may still log its connect timeout to the old address
        # just before that line, and that is not a new connection.
        ids = []
        with gw() as c:
            end = time.monotonic() + 15
            while time.monotonic() < end:
                request_id, r = get_notifications(c, customer.headers, "after")
                assert (r.status_code, r.json()) == (502, UNAVAILABLE)
                ids.append(request_id)
                time.sleep(0.5)
        log = wait_until(lambda: (lambda g: g if all(i in g for i in ids) else None)(gateway_log()), 15,
                         interval=1, what="log lines")
        assert {log[i]["upstream_addr"] for i in ids} == {GROUP}
        assert {log[i]["upstream_status"] for i in ids} == {"502"}
        late_errors = gateway_errors_after(first_updated, since)
        assert late_errors, "the error log after the update must not be empty (no live upstreams)"
        assert not [e for e in late_errors if f"{old_ip}:8000" in e], late_errors
        print(f"after the update: {len(ids)} requests, upstream_addr={GROUP}, "
              f"0 error-log lines with {old_ip}:8000")
    finally:
        # 7. Recovery
        back = restore_notification_service(customer)

    # 8. Routed again to the current address, without restarting nginx
    current = ips("notification-service")
    entry = log_entry(back)
    assert entry["upstream_addr"].split(":")[0] in current and entry["upstream_status"] == "200"
    print(f"recovered: upstream_addr={entry['upstream_addr']} (before the stop {old_ip}:8000)")
    assert gateway_identity() == before


def test_8_connection_refused_gives_json_502(customer):
    """A container answers to the name but nothing listens on port 8000."""
    before = gateway_identity()
    gateway = inspect(containers("gateway")[0]["Name"])
    network = next(n for n in gateway["NetworkSettings"]["Networks"] if n.endswith("_ecommerce-net"))
    image = inspect(containers("notification-service")[0]["Name"])["Config"]["Image"]
    name = "gwt-refuse"
    run("docker", "rm", "-f", name, check=False)
    since = utc_now()
    compose("stop", "notification-service")
    try:
        run("docker", "run", "-d", "--rm", "--name", name, "--network", network,
            "--network-alias", "notification-service", image, "python", "-c", "import time; time.sleep(600)")
        sleeper_ip = inspect(name)["NetworkSettings"]["Networks"][network]["IPAddress"]
        with gw() as c:
            def refused():
                request_id, r = get_notifications(c, customer.headers, "refused")
                entry = log_entry(request_id)
                return (r, entry) if entry["upstream_addr"] == f"{sleeper_ip}:8000" else None

            r, entry = wait_until(refused, 40, interval=1, what="nginx to use the new address")
        print(f"\nrefused: status={r.status_code} upstream_addr={entry['upstream_addr']} "
              f"upstream_status={entry['upstream_status']}")
        assert (r.status_code, r.json()) == (502, UNAVAILABLE)
        assert "content-security-policy" in r.headers and r.headers["x-content-type-options"] == "nosniff"
        assert entry["upstream_status"] == "502"
        assert any("Connection refused" in e and sleeper_ip in e for e in gateway_errors(since))
    finally:
        run("docker", "rm", "-f", name, check=False)
        restore_notification_service(customer)
    assert gateway_identity() == before


# 9 ------------------------------------------------------------------------------------------
def test_9_frozen_service_gives_json_504(customer):
    """Paused container: the connection is accepted by the kernel but the
    service never answers, so proxy_read_timeout (30 s) applies."""
    before = gateway_identity()
    with gw() as c:
        assert get_notifications(c, customer.headers, "before-pause")[1].status_code == 200
    since = utc_now()
    compose("pause", "notification-service")
    try:
        request_id = rid("timeout")
        start = time.monotonic()
        with gw(timeout=90) as c:
            r = c.get("/api/notifications", headers={**customer.headers, "X-Request-ID": request_id})
        elapsed = time.monotonic() - start
        entry = log_entry(request_id)
        print(f"\n504 after {elapsed:.1f} s: upstream_addr={entry['upstream_addr']} "
              f"upstream_status={entry['upstream_status']} connect_time={entry['upstream_connect_time']}")
        assert (r.status_code, r.json()) == (504, TIMEOUT)
        assert r.headers["x-request-id"] == request_id and "content-security-policy" in r.headers
        assert 28 <= elapsed <= 40
        assert entry["upstream_connect_time"] != "-"      # connected: this is a read timeout
        assert "," not in entry["upstream_addr"]           # a single attempt, not repeated
        assert any("upstream timed out" in e and "reading response header" in e for e in gateway_errors(since))
    finally:
        restore_notification_service(customer)
    assert gateway_identity() == before
