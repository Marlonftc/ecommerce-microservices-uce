"""Healthcheck of order-worker (it has no HTTP port).

Exit code 0 if the heartbeat file was touched recently, 1 otherwise.
"""
import sys
import time
from pathlib import Path

from app.config import settings


def main() -> int:
    try:
        age = time.time() - float(Path(settings.worker_heartbeat_file).read_text())
    except (OSError, ValueError):
        return 1
    return 0 if age < settings.worker_heartbeat_max_age_seconds else 1


if __name__ == "__main__":
    sys.exit(main())
