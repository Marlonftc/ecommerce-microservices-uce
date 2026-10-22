"""order-worker: python -m app.worker

Every WORKER_POLL_SECONDS it runs one cycle (run_once):
  1. expire PENDING_PAYMENT orders whose time ran out
  2. cancel RESERVING orders older than RESERVING_SWEEP_MINUTES
  3. run up to 20 due operations (PENDING, or FAILED/TRANSIENT in the slow lane)

Several workers can run at the same time: SKIP LOCKED and the lease keep
them from taking the same row. External calls can still be repeated (a
worker may stop after the call and before recording it); every call is
idempotent, so a repeated call has no new effect.

Health: the worker touches WORKER_HEARTBEAT_FILE after reaching the database
in each cycle and before each operation. A call lasts at most a few seconds,
so a healthy worker never goes WORKER_HEARTBEAT_MAX_AGE_SECONDS without a
heartbeat; a stuck one (or one that cannot reach the database) does.

SIGTERM / SIGINT (and CTRL+BREAK on Windows): no new operation is claimed,
the current one finishes, the connections are closed and the process exits.
If the process is killed instead, the leases expire and another worker
takes over the operations.
"""
import logging
import signal
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine
from app.services import executor, recovery

logger = logging.getLogger("order-worker")

BATCH = 20


class Worker:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        self.stopping = threading.Event()

    def request_stop(self, signum=None, frame=None) -> None:
        if not self.stopping.is_set():
            logger.info("stop requested: finishing the current work")
        self.stopping.set()

    @staticmethod
    def heartbeat() -> None:
        try:
            Path(settings.worker_heartbeat_file).write_text(str(time.time()))
        except OSError:
            logger.warning("cannot write heartbeat file %s", settings.worker_heartbeat_file)

    def run_once(self) -> dict[str, int]:
        stats = {"expired": 0, "swept": 0, "done": 0, "retry": 0, "failed": 0, "lost": 0}
        with self.session_factory() as db:
            stats["expired"] = recovery.expire_orders(db)
            stats["swept"] = recovery.sweep_reserving(db)
            self.heartbeat()  # the database answered: the worker makes progress
            for _ in range(BATCH):
                if self.stopping.is_set():
                    break  # nothing more is claimed after a stop request
                self.heartbeat()
                result = self._run_next(db)
                if result is None:
                    break
                stats[result.lower()] = stats.get(result.lower(), 0) + 1
        return stats

    @staticmethod
    def _run_next(db: Session) -> str | None:
        claimed = executor.claim_next(db)
        if claimed is None:
            return None
        return executor.execute(db, claimed)

    def run_forever(self) -> None:
        logger.info("worker started (poll %ss, lease %ss)", settings.worker_poll_seconds,
                    settings.worker_lease_seconds)
        while not self.stopping.is_set():
            try:
                stats = self.run_once()
                if any(stats.values()):
                    logger.info("cycle: %s", stats)
            except Exception:
                # A database outage must not kill the worker: it tries again next cycle
                logger.exception("cycle failed")
            self.stopping.wait(settings.worker_poll_seconds)
        engine.dispose()
        logger.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = Worker()
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), worker.request_stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
