"""
Test settings: identical to settings.py except the database is SQLite
in-memory, so `pytest` / `manage.py test` can run in CI or a fresh clone
without a local PostgreSQL server. Production and local dev should always
use itms_project.settings (Postgres) -- this module exists purely to keep
the automated test suite fast and dependency-free.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Failure injection off by default for deterministic tests
ITMS_FAILURE_INJECTION_RATE = 0.0
ITMS_SIMULATED_LATENCY_MS = 0
