"""Fixed migration/pytest entry point inside the temporary source copy."""
import json
from pathlib import Path
import sys

import isolation_guard as guard


def main():
    policy = json.loads(Path(__file__).with_name("guard-policy.json").read_text(encoding="utf-8"))
    guard.verify_installed(policy)
    import psycopg
    import os

    # A real guarded preflight must succeed before migrations or collection.
    with psycopg.connect(host="127.0.0.1", port=55433, dbname=policy["database"],
                         user=policy["role"], password=os.environ["DB_PASSWORD"], connect_timeout=3):
        pass
    mode = sys.argv[1]
    if mode == "migrate":
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")
        return 0
    if mode != "test" or len(sys.argv) != 4:
        guard.violation(policy, "Unexpected child command")
    import pytest

    class RequiredIsolation:
        def pytest_runtest_setup(self, item):
            guard.verify_installed(policy)

        def pytest_sessionfinish(self, session, exitstatus):
            guard.verify_installed(policy)

    return int(pytest.main(["tests", "-q", "-p", "no:cacheprovider", "--tb=short",
                           "--junitxml=" + sys.argv[2], "--basetemp=" + sys.argv[3]],
                          plugins=[RequiredIsolation()]))


if __name__ == "__main__":
    raise SystemExit(main())
