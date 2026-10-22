import pytest

from support import ENV, GATEWAY, PORTS, admin, http


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: stops, pauses or scales containers")


@pytest.fixture(scope="session", autouse=True)
def system_is_running():
    if not ENV.get("ADMIN_EMAIL") or not ENV.get("ADMIN_PASSWORD"):
        pytest.exit("ADMIN_EMAIL and ADMIN_PASSWORD must be set in the root .env", returncode=2)
    for service in PORTS:
        try:
            with http(service) as c:
                ready = c.get("/health/ready").status_code == 200
        except Exception:
            ready = False
        if not ready:
            pytest.exit(f"{service}-service is not ready on 127.0.0.1:{PORTS[service]}. Start the "
                        "system with compose.yaml + compose.override.example.yaml + compose.e2e.yaml",
                        returncode=2)
    try:
        with http("product") as c:
            ready = c.get("/products", params={"limit": 1}).status_code == 200
    except Exception:
        ready = False
    if not ready:
        pytest.exit(f"product-service is not reachable through the gateway ({GATEWAY}/api/products)",
                    returncode=2)


@pytest.fixture(scope="session")
def adm():
    return admin()
