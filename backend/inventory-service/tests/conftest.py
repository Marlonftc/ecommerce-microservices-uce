import os
from pathlib import Path

# Without a local .env (for example on another machine), use dummy values.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")
    os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
