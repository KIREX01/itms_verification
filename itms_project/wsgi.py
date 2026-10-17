import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "itms_project.settings")

application = get_wsgi_application()

try:
    from core.services import config_service
    if config_service.get_setting("sync.auto_sync_enabled", True):
        from core.services.order_sync import ShiftOrderSyncDaemon
        from core.services import kit_provisioning_service
        ShiftOrderSyncDaemon.get_instance().start()
        kit_provisioning_service.MorningKitSyncDaemon.get_instance().start()
except Exception:
    pass
