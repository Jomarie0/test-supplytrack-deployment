from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"

    def ready(self):
        # import signals to register them
        try:
            from . import signals  # noqa: F401
        except Exception:
            # ignore import errors during management commands if needed
            pass
