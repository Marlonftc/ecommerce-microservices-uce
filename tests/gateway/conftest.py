import pytest

from support import ENV, GATEWAY, admin, compose, gw, new_customer, run


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: stops, pauses or scales containers")


@pytest.fixture(scope="session", autouse=True)
def system_is_running():
    if not ENV.get("ADMIN_EMAIL") or not ENV.get("ADMIN_PASSWORD"):
        pytest.exit("ADMIN_EMAIL and ADMIN_PASSWORD must be set in the root .env", returncode=2)
    try:
        with gw() as c:
            ready = c.get("/health").status_code == 200 and c.get("/api/products").status_code == 200
    except Exception:
        ready = False
    if not ready:
        pytest.exit(f"The gateway does not answer on {GATEWAY}. Start the system with "
                    "compose.yaml + compose.override.example.yaml", returncode=2)
    yield
    # Leave the system as it was, even if a test failed half way
    run("docker", "rm", "-f", "gwt-header-echo", check=False)
    compose("unpause", "notification-service", check=False)
    compose("start", "notification-service", "order-worker", check=False)
    compose("up", "-d", "--no-deps", "--no-recreate", "--scale", "product-service=1", "product-service",
            check=False)


@pytest.fixture(scope="session")
def adm():
    return admin()


@pytest.fixture(scope="session")
def customer():
    return new_customer()
