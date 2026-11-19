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

# Isolate secure storage (config.json, auth cookies) and the export directory in a
# throwaway folder so tests never rewrite the developer's real secure/config.json
# or leave stale cache files (e.g. .blocked_kits_*.json) in the real exports folder.
import json as _json
import os as _os
import tempfile as _tempfile

SECURE_STORAGE_DIR = _tempfile.mkdtemp(prefix="itms_test_secure_")
_TEST_EXPORT_DIR = _os.path.join(SECURE_STORAGE_DIR, "exports")
_os.makedirs(_TEST_EXPORT_DIR, exist_ok=True)
with open(_os.path.join(SECURE_STORAGE_DIR, "config.json"), "w", encoding="utf-8") as _f:
    _json.dump({"sync": {"default_export_directory": _TEST_EXPORT_DIR}}, _f)

# Never call the live Resend email API from tests; email_service falls back to its
# simulated dev mode when no API key is configured.
RESEND_API_KEY = ""
_os.environ.pop("RESEND_API_KEY", None)

# Failure injection off by default for deterministic tests
ITMS_FAILURE_INJECTION_RATE = 0.0
ITMS_SIMULATED_LATENCY_MS = 0
TESTING = False

# Legacy suites predate role-based access; governance tests enable enforcement explicitly
# with @override_settings(GOVERNANCE_ENFORCE=True).
GOVERNANCE_ENFORCE = False
