import os
from pathlib import Path

# Without a local .env (for example on another machine), use a dummy password.
if not Path(".env").exists():
    os.environ.setdefault("DB_PASSWORD", "test-password")

# Fixed internal key for the tests (overrides a local .env)
os.environ["INTERNAL_API_KEY"] = "test-internal-key"
