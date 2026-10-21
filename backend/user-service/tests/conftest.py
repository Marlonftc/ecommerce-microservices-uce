import os

# Tests must not depend on a real ".env" file
os.environ.setdefault("DB_PASSWORD", "test-password")
