# inventory/signals.py
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Product, DemandCheckLog, StockMovement
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.utils import timezone
from datetime import timedelta

"""
Signals for inventory events.

Note: Do not import view functions here or call them directly. Views expect a request
object and importing them can create circular dependencies. Keep signals focused on
model updates and messaging only.
"""


@receiver(post_save, sender=DemandCheckLog)
def send_restock_notification(sender, instance, created, **kwargs):
    """
    Send real-time notification when a new restock log is created
    and restock is needed.
    """
    if created and instance.restock_needed:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                "notifications",
                {
                    "type": "restock_notification",
                    "message": {
                        "id": instance.id,
                        "product_name": instance.product.name,
                        "product_id": instance.product.product_id,
                        "forecasted_quantity": instance.forecasted_quantity,
                        "current_stock": instance.current_stock,
                        "message": f"Restock needed for {instance.product.name}. Current: {instance.current_stock}, Forecasted demand: {instance.forecasted_quantity}",
                        "timestamp": instance.checked_at.isoformat(),
                        "type": "restock_alert",
                    },
                },
            )


@receiver(post_save, sender=StockMovement)
def update_restock_logs_on_stock_change(sender, instance, created, **kwargs):
    """
    Update restock logs when stock is increased (movement_type == "IN").
    Resolve logs if stock now covers forecasted quantity.
    """
    if created and instance.movement_type == "IN":
        product = instance.product
        pending_logs = DemandCheckLog.objects.filter(
            product=product, restock_needed=True, is_deleted=False
        )

        for log in pending_logs:
            log.current_stock = product.stock_quantity
            if product.stock_quantity >= log.forecasted_quantity:
                # Soft delete (mark resolved)
                log.is_deleted = True
                log.save(update_fields=["is_deleted"])

                # Send resolution notification
                channel_layer = get_channel_layer()
                if channel_layer:
                    async_to_sync(channel_layer.group_send)(
                        "notifications",
                        {
                            "type": "restock_resolved",
                            "message": {
                                "id": log.id,
                                "product_name": log.product.name,
                                "message": f"Restock issue resolved for {log.product.name}",
                                "type": "restock_resolved",
                            },
                        },
                    )
            else:
                log.save(update_fields=["current_stock"])


@receiver(post_save, sender=Product)
def update_restock_logs_on_product_save(sender, instance, created, **kwargs):
    """
    Update restock logs when product stock is updated directly (not just through StockMovement).
    """
    if created:
        # Newly created product — no need to update logs yet
        return

    pending_logs = DemandCheckLog.objects.filter(
        product=instance, restock_needed=True, is_deleted=False
    )

    for log in pending_logs:
        old_stock = log.current_stock
        new_stock = instance.stock_quantity

        if old_stock != new_stock:
            log.current_stock = new_stock
            if new_stock >= log.forecasted_quantity:
                # Soft delete (mark resolved)
                log.is_deleted = True
                log.save(update_fields=["is_deleted"])

                # Send resolution notification
                channel_layer = get_channel_layer()
                if channel_layer:
                    async_to_sync(channel_layer.group_send)(
                        "notifications",
                        {
                            "type": "restock_resolved",
                            "message": {
                                "id": log.id,
                                "product_name": log.product.name,
                                "message": f"Restock issue resolved for {log.product.name}",
                                "type": "restock_resolved",
                            },
                        },
                    )
            else:
                log.save(update_fields=["current_stock"])


@receiver(pre_save, sender=Product)
def check_stock_threshold(sender, instance, **kwargs):
    """
    Before saving a product, check if stock dropped below reorder level.
    If so, create a restock log unless one exists in last 24 hours.
    """
    if not instance.pk:
        # New product, no old stock to compare
        return

    try:
        old_instance = Product.objects.get(pk=instance.pk)
    except Product.DoesNotExist:
        return

    # Check if stock crosses threshold downward
    if (
        old_instance.stock_quantity > old_instance.reorder_level
        and instance.stock_quantity <= instance.reorder_level
    ):

        # Check if a recent log exists (last 24 hours)
        recent_log = DemandCheckLog.objects.filter(
            product=instance,
            restock_needed=True,
            is_deleted=False,
            checked_at__gte=timezone.now() - timedelta(seconds=3),
        ).first()

        if not recent_log:
            # Create new restock log with suggested forecast quantity
            DemandCheckLog.objects.create(
                product=instance,
                forecasted_quantity=instance.reorder_level
                + 50,  # You can adjust this logic
                current_stock=instance.stock_quantity,
                restock_needed=True,
            )


@receiver(post_save, sender="orders.OrderItem")
def update_reorder_level_on_sales(sender, instance, created, **kwargs):
    """
    Update reorder level for a product when new sales data is available.
    This runs when a new OrderItem is created (new sale).
    """
    if created and instance.order and instance.order.status == "Completed":
        try:
            # Get the product from the order item
            product = instance.product_variant.product

            # Only update if the product exists and is not deleted
            if product and not product.is_deleted:
                # Check if we should update reorder level (e.g., every 10 sales)
                # This prevents too frequent updates
                from apps.orders.models import OrderItem

                recent_sales_count = OrderItem.objects.filter(
                    product_variant__product=product,
                    order__is_deleted=False,
                    order__status="Completed",
                    order__order_date__gte=timezone.now() - timedelta(days=1),
                ).count()

                # Update reorder level every 5 sales or once per day
                if recent_sales_count % 5 == 0 or recent_sales_count == 1:
                    # Use a background task or delay to avoid blocking the request

                    try:
                        # Update reorder level for this specific product
                        success, new_reorder_level, forecast_quantity, error = (
                            product.update_dynamic_reorder_level()
                        )

                        if success:
                            # Log the update (optional)
                            print(
                                f"Updated reorder level for {product.name}: {new_reorder_level} (Forecast: {forecast_quantity})"
                            )
                        else:
                            print(
                                f"Failed to update reorder level for {product.name}: {error}"
                            )

                    except Exception as e:
                        print(
                            f"Error updating reorder level for {product.name}: {str(e)}"
                        )

        except Exception as e:
            # Silently fail to avoid breaking the order process
            print(f"Error in reorder level update signal: {str(e)}")


from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.inventory.models import StockMovement
from apps.transactions.models import log_audit
from apps.transactions.middleware import get_current_request


@receiver(post_save, sender=StockMovement)
def log_stock_movement(sender, instance, created, **kwargs):
    """
    Log stock movement creation/update. Uses threadlocal request if available.
    Keep the payload small.
    """
    req = get_current_request()
    user = getattr(req, "user", None) if req else None
    action = "create" if created else "update"
    try:
        log_audit(
            user=user,
            action=action,
            instance=instance,
            changes={
                "movement_type": instance.movement_type,
                "quantity": instance.quantity,
            },
            request=req,
            extra={"product_id": getattr(instance.product, "id", None)},
        )
    except Exception:
        # do not break business flow if logging fails
        pass
