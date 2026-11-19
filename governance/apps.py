from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "governance"
    verbose_name = "Governance (accounts, roles, policy)"

    def ready(self):
        from governance import signals  # noqa: F401  (session index + account state hooks)
