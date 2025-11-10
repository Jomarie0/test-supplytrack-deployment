# apps/purchasing/apps.py
from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.purchase_orders"  # This must match the app folder name

    def ready(self):
        # Import signals here so they are registered when Django starts
        import apps.purchase_orders.signals  # noqa: F401
