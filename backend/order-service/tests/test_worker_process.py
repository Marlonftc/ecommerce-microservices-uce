"""18-19. The real worker process: it finishes saved work started by an
earlier process and stops cleanly on SIGTERM (CTRL+BREAK on Windows).

notification-service is replaced by a tiny local HTTP server; payment and
inventory are not called in this test.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal, engine
from app.models import Order, PendingOperation

USER = 900901


class Notifications(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Notifications.received.append(body)
        payload = json.dumps({"id": str(uuid.uuid4())}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def saved_notification():
    """An order and a pending ORDER_PAID notification, as left by an earlier process."""
    try:
        engine.connect().close()
    except Exception:
        pytest.skip("database not available")
    order_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Order(id=order_id, user_id=USER, status="PAID", total="10.00", checkout_key="k",
                     cart_version=1, inventory_status="CONFIRMED", payment_key="p",
                     payment_simulation="APPROVE", payment_status="APPROVED"))
        db.flush()
        db.add(PendingOperation(order_id=order_id, kind="SEND_NOTIFICATION",
                                operation_key=f"{order_id}:ORDER_PAID",
                                payload={"event_type": "ORDER_PAID", "user_id": USER,
                                         "amount": "10.00", "currency": "USD"}))
        db.commit()
    yield order_id
    with SessionLocal() as db:
        db.execute(delete(Order).where(Order.user_id == USER))
        db.commit()


def test_worker_process_resumes_saved_work_and_stops_on_sigterm(saved_notification):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Notifications)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "WORKER_POLL_SECONDS": "0.3",
           "NOTIFICATION_SERVICE_URL": f"http://127.0.0.1:{server.server_address[1]}"}
    windows = sys.platform == "win32"
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0)
    try:
        status = None
        for _ in range(100):
            with SessionLocal() as db:
                status = db.scalar(select(PendingOperation.status)
                                   .where(PendingOperation.order_id == saved_notification))
            if status == "DONE":
                break
            time.sleep(0.1)
        assert status == "DONE"
        assert any(b["event_id"] == f"{saved_notification}:ORDER_PAID" for b in Notifications.received)

        process.send_signal(signal.CTRL_BREAK_EVENT if windows else signal.SIGTERM)
        output, _ = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
        server.shutdown()

    assert process.returncode == 0
    assert "worker started" in output
    assert "stop requested" in output
    assert "worker stopped" in output
