# apps/delivery/apps.py

from django.apps import AppConfig


class DeliveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.delivery"

    def ready(self):
        # FIX: Import your signals to ensure they are registered with Django.
        # This is where Django connects your signal functions to model events.
        try:
            import apps.delivery.signals  
        except ImportError:
            # Handle case where signals.py might not exist yet
            pass