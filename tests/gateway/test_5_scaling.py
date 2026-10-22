"""Checks 4, 6 and 5: product-service scaled with --scale while nginx keeps running.

X-Instance is the container hostname (its short id), different for each replica.
The gateway container must keep the same start time: no restart and no reload
is done by the test. The tests run in file order: 3 replicas, one stopped, back to 1.
"""
import collections

import pytest

from support import compose, containers, gateway_log, gw, inspect, ips, rid, run, running, wait_healthy, wait_until

pytestmark = pytest.mark.docker


def scale(n: int) -> None:
    compose("up", "-d", "--no-deps", "--no-recreate", "--scale", f"product-service={n}", "product-service")


def instances(n: int = 30) -> collections.Counter:
    seen = collections.Counter()
    with gw() as c:
        for _ in range(n):
            r = c.get("/api/products", params={"limit": 1})
            seen[r.headers.get("x-instance") if r.status_code == 200 else f"HTTP {r.status_code}"] += 1
    return seen


def hostnames() -> set[str]:
    return {inspect(c["Name"])["Config"]["Hostname"] for c in running("product-service")}


def gateway_state() -> tuple[str, str, int]:
    g = inspect(containers("gateway")[0]["Name"])
    return g["Id"], g["State"]["StartedAt"], g["RestartCount"]


@pytest.fixture(scope="module")
def gateway_before():
    assert not compose("config", "--format", "json").stdout.count('"container_name"'), "container_name blocks --scale"
    assert len(running("product-service")) == 1, "start with one product-service replica"
    return gateway_state()


# 4 ------------------------------------------------------------------------------------------
def test_4_three_replicas_without_restarting_nginx(gateway_before):
    first = instances(10)
    assert len(first) == 1

    scale(3)
    wait_healthy("product-service", 3)
    names = hostnames()
    assert len(names) == 3

    # Docker DNS answers with 3 addresses; nginx refreshes every 10 s
    wait_until(lambda: len(instances(9)) == 3, 40, interval=1, what="3 different X-Instance values")
    seen = instances(30)
    print(f"\n3 replicas -> {dict(seen)}")
    assert set(seen) == names                     # only real replicas, all of them, no errors
    assert min(seen.values()) >= 5                # round robin spreads the requests
    assert gateway_state() == gateway_before


# 6 ------------------------------------------------------------------------------------------
def test_6_one_replica_down_the_others_keep_answering(gateway_before):
    victim = running("product-service")[-1]["Name"]
    victim_host = inspect(victim)["Config"]["Hostname"]
    run("docker", "stop", victim)

    # Right after the stop the DNS cache may still list it: a refused
    # connection is retried on another replica (GET only), so no error reaches the client
    seen = instances(30)
    print(f"\none replica stopped -> {dict(seen)}")
    assert victim_host not in seen
    assert all(not str(k).startswith("HTTP") for k in seen), seen
    assert len(seen) == 2
    assert gateway_state() == gateway_before


# 5 ------------------------------------------------------------------------------------------
def test_5_back_to_one_replica_old_addresses_are_dropped(gateway_before):
    scale(1)
    wait_until(lambda: len(running("product-service")) == 1, 60, what="one replica")
    wait_healthy("product-service", 1)
    remaining = hostnames()
    remaining_ip = set(ips("product-service"))

    wait_until(lambda: set(instances(9)) == remaining, 40, interval=1, what="only the remaining replica")
    ids = []
    with gw() as c:
        for _ in range(20):
            request_id = rid("scale1")
            r = c.get("/api/products", params={"limit": 1}, headers={"X-Request-ID": request_id})
            assert r.status_code == 200 and r.headers["x-instance"] in remaining
            ids.append(request_id)
    log = wait_until(lambda: (lambda g: g if all(i in g for i in ids) else None)(gateway_log()), 15,
                     interval=1, what="log lines")
    used = {log[i]["upstream_addr"].split(":")[0] for i in ids}
    print(f"\n1 replica -> upstream {used}")
    assert used == remaining_ip                   # removed addresses are no longer used
    assert gateway_state() == gateway_before
