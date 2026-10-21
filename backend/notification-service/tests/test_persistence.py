"""The data survives a restart of the application process.

Starts the real service with uvicorn, registers and reads a notification,
stops the process, starts a new one and checks that everything is still there.
"""
import os
import socket
import subprocess
import sys
import time
import uuid

import httpx2
import pytest
from sqlalchemy import delete

from app.database import SessionLocal, engine
from app.models import Notification
from tests.conftest import INTERNAL, bearer, event_body


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_service(port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            if httpx2.get(f"http://127.0.0.1:{port}/health/ready").status_code == 200:
                return process
        except httpx2.TransportError:
            pass
        time.sleep(0.1)
    process.kill()
    raise RuntimeError("the service did not start")


def stop_service(process: subprocess.Popen) -> None:
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
def order_id():
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    value = uuid.uuid4()
    yield value
    with SessionLocal() as db:
        db.execute(delete(Notification).where(Notification.order_id == value))
        db.commit()


def test_notification_survives_a_restart(order_id):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    body = event_body(order_id=order_id, user_id=7)

    first = start_service(port)
    try:
        created = httpx2.post(f"{url}/internal/notifications", json=body, headers=INTERNAL).json()
        read = httpx2.patch(f"{url}/notifications/{created['id']}/read", headers=bearer(7)).json()
    finally:
        stop_service(first)

    second = start_service(port)  # a new process: nothing kept in memory
    try:
        items = httpx2.get(f"{url}/notifications", headers=bearer(7)).json()["items"]
        repeated = httpx2.post(f"{url}/internal/notifications", json=body, headers=INTERNAL)
    finally:
        stop_service(second)

    assert second.pid != first.pid
    after = next(n for n in items if n["id"] == created["id"])
    assert after == read  # same texts, read state and dates
    assert repeated.status_code == 200  # idempotency also survives the restart
    assert repeated.json() == read
