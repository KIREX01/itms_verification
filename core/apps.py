import os
from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _configure_sqlite_connection(sender, connection, **kwargs):
    """Configures SQLite connections with WAL mode and high busy-timeout for concurrent access."""
    if connection.vendor == "sqlite":
        try:
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA busy_timeout = 60000;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            cursor.execute("PRAGMA cache_size = -64000;")
        except Exception:
            pass


def _patch_werkzeug_reloader_fd():
    """Fixes KeyError: 'WERKZEUG_SERVER_FD' in django-extensions runserver_plus with Werkzeug 3.x."""
    try:
        import werkzeug.serving
        _orig_is_running_from_reloader = werkzeug.serving.is_running_from_reloader

        def _safe_is_running_from_reloader():
            if os.environ.get("WERKZEUG_RUN_MAIN") == "true" and "WERKZEUG_SERVER_FD" not in os.environ:
                return False
            return _orig_is_running_from_reloader()

        werkzeug.serving.is_running_from_reloader = _safe_is_running_from_reloader
    except Exception:
        pass


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "ITMS Installation Verification"

    def ready(self):
        connection_created.connect(_configure_sqlite_connection)
        _patch_werkzeug_reloader_fd()
