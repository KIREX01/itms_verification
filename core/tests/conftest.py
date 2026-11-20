"""
Shared pytest fixtures for the CLOSE Copilot test suite.

Every test starts from the same clean, isolated config (see itms_project/settings_test.py:
SECURE_STORAGE_DIR). Without this, a test that toggles a persisted setting — e.g. pausing
the ITMS master switch or enabling maintenance mode — and fails before restoring it would
make unrelated later tests fail with HTTP 503.
"""
import json
import os
import shutil

import pytest
from django.conf import settings


@pytest.fixture(autouse=True)
def _clear_cache():
    """Process caches (e.g. role capabilities) must not leak between tests that roll back the DB."""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _reset_isolated_config():
    secure_dir = getattr(settings, "SECURE_STORAGE_DIR", None)
    if not secure_dir:
        # Never touch real storage: only reset when the test settings isolate it.
        yield
        return

    export_dir = os.path.join(secure_dir, "exports")
    os.makedirs(export_dir, exist_ok=True)
    with open(os.path.join(secure_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"sync": {"default_export_directory": export_dir}}, f)

    flag = os.path.join(secure_dir, "maintenance.flag")
    if os.path.isfile(flag):
        os.remove(flag)

    # Drop file caches (e.g. .blocked_kits_<suffix>.json) left by a previous test.
    for name in os.listdir(export_dir):
        path = os.path.join(export_dir, name)
        if os.path.isfile(path):
            os.remove(path)
        else:
            shutil.rmtree(path, ignore_errors=True)
    yield
