import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    """Process caches (e.g. role capabilities) must not leak between tests that roll back the DB."""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()
