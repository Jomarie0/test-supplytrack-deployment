# apps/orders/apps.py

from django.apps import AppConfig

class OrdersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.orders'

    def ready(self):
        # Import your signals file here to ensure they are registered
        # The import needs to happen inside ready() to avoid circular imports.
        import apps.orders.signals  # <--- THIS IS THE CRITICAL LINE