"""The migration job must work with the database variables only."""
import os
import subprocess
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]


def import_in_clean_process(module: str, tmp_path) -> subprocess.CompletedProcess:
    # Only database variables. The working directory has no .env file.
    env = {k: v for k, v in os.environ.items()
           if k not in ("INTERNAL_API_KEY", "JWT_PUBLIC_KEY_PATH") and not k.startswith("DB_")}
    env.update(DB_PASSWORD="only-db-password", PYTHONPATH=str(SERVICE_DIR))
    return subprocess.run([sys.executable, "-c", f"import {module}"], cwd=tmp_path, env=env,
                          capture_output=True, text=True)


def test_migrations_need_only_database_variables(tmp_path):
    # alembic/env.py imports app.models, which imports app.database
    result = import_in_clean_process("app.models", tmp_path)

    assert result.returncode == 0, result.stderr


def test_the_api_still_requires_the_internal_key(tmp_path):
    result = import_in_clean_process("app.main", tmp_path)

    assert result.returncode != 0
    assert "internal_api_key" in result.stderr
    assert "only-db-password" not in result.stderr  # the password is never printed
