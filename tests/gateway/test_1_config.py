"""Checks 7, 12, 15 and 16: configuration, isolation, health and nginx version (gateway and frontend)."""
import json
import socket

from support import GATEWAY, SERVICES, compose, containers, gw, inspect, run


# 15 -----------------------------------------------------------------------------------------
def test_15_compose_config_and_nginx_t():
    assert compose("config", "--quiet").returncode == 0
    # compose.yaml alone publishes only the gateway, on 127.0.0.1
    config = json.loads(run("docker", "compose", "-f", "compose.yaml", "config", "--format", "json").stdout)
    published = {name: svc["ports"] for name, svc in config["services"].items() if svc.get("ports")}
    assert list(published) == ["gateway"]
    assert published["gateway"][0]["host_ip"] == "127.0.0.1"
    assert published["gateway"][0]["target"] == 80

    result = compose("exec", "-T", "gateway", "nginx", "-t")
    assert "syntax is ok" in result.stderr and "test is successful" in result.stderr


# 16 -----------------------------------------------------------------------------------------
def test_16_gateway_and_services_are_healthy():
    with gw() as c:
        health = c.get("/health")
    assert (health.status_code, health.json()) == (200, {"status": "ok"})
    for service in ["gateway", "frontend", *SERVICES, "order-worker"]:
        states = [(c["State"], c.get("Health")) for c in containers(service)]
        assert states and all(s == ("running", "healthy") for s in states), (service, states)
    gateway = inspect(containers("gateway")[0]["Name"])
    assert gateway["HostConfig"]["ReadonlyRootfs"] is True


# 7 ------------------------------------------------------------------------------------------
def test_7_gateway_only_in_ecommerce_net_without_credentials():
    gateway = inspect(containers("gateway")[0]["Name"])
    networks = list(gateway["NetworkSettings"]["Networks"])
    assert len(networks) == 1 and networks[0].endswith("_ecommerce-net"), networks
    postgres = inspect(containers("postgres")[0]["Name"])
    assert not any(n.endswith("_ecommerce-net") for n in postgres["NetworkSettings"]["Networks"])

    # Names only: no value is printed or compared
    names = [e.split("=", 1)[0] for e in gateway["Config"]["Env"]]
    assert not [n for n in names if n.startswith(("DB_", "POSTGRES", "INTERNAL", "JWT", "ADMIN"))
                or "PASSWORD" in n or "KEY" in n], names
    assert not [m for m in gateway["Mounts"] if "secrets" in m.get("Destination", "")]

    # From inside the gateway: the services are reachable, PostgreSQL does not even resolve
    ok = compose("exec", "-T", "gateway", "wget", "-q", "-O", "-", "-T", "3",
                 "http://product-service:8000/health/live", check=False)
    assert ok.returncode == 0 and "status" in ok.stdout
    db = compose("exec", "-T", "gateway", "wget", "-q", "-O", "-", "-T", "3", "http://postgres:5432/",
                 check=False)
    assert db.returncode != 0 and "bad address" in db.stderr, db.stderr


# 12 -----------------------------------------------------------------------------------------
def test_12_no_nginx_version_exposed():
    with gw() as c:
        for path in ["/health", "/api/products", "/api/unknown"]:
            response = c.get(path)
            assert response.headers["server"] == "nginx", path
            assert "nginx/" not in response.text

    # A malformed request gets the built-in HTML error page of nginx
    host, port = GATEWAY.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as s:
        s.sendall(b"NOT-HTTP\r\n\r\n")
        raw = s.recv(4096).decode(errors="replace")
    assert raw.startswith("HTTP/1.1 400")
    assert "Server: nginx\r\n" in raw and "nginx/" not in raw


# 7 (frontend) -------------------------------------------------------------------------------
def test_7_frontend_isolated_unprivileged_and_read_only():
    frontend = inspect(containers("frontend")[0]["Name"])
    networks = list(frontend["NetworkSettings"]["Networks"])
    assert len(networks) == 1 and networks[0].endswith("_ecommerce-net"), networks
    host = frontend["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
    assert "no-new-privileges:true" in (host.get("SecurityOpt") or [])
    assert not host.get("PortBindings")          # reached only through the gateway

    # Names only: no value is printed or compared
    names = [e.split("=", 1)[0] for e in frontend["Config"]["Env"]]
    assert not [n for n in names if n.startswith(("DB_", "POSTGRES", "INTERNAL", "JWT", "ADMIN"))
                or "PASSWORD" in n or "KEY" in n or "SECRET" in n], names
    assert not [m for m in frontend["Mounts"] if "secrets" in m.get("Destination", "")]

    # nginx runs as uid 101, not root
    assert compose("exec", "-T", "frontend", "id", "-u").stdout.strip() == "101"
    # PostgreSQL does not even resolve from the frontend
    db = compose("exec", "-T", "frontend", "wget", "-q", "-O", "-", "-T", "3", "http://postgres:5432/",
                 check=False)
    assert db.returncode != 0 and "bad address" in db.stderr, db.stderr
