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
    verbose_name = "CLOSE Copilot"

    def ready(self):
        connection_created.connect(_configure_sqlite_connection)
        _patch_werkzeug_reloader_fd()
        # The per-request device blocklist is cached; any device change clears it immediately.
        from django.db.models.signals import post_delete, post_save
        from core.models import AuthorizedDevice
        from core.services.device_access_service import invalidate_blocklist
        post_save.connect(invalidate_blocklist, sender=AuthorizedDevice, dispatch_uid="close-blocklist-save")
        post_delete.connect(invalidate_blocklist, sender=AuthorizedDevice, dispatch_uid="close-blocklist-delete")
