# apps/purchasing/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderNotification
from apps.inventory.models import StockMovement


def _create_po_notification(po: PurchaseOrder, action_label: str):
    """Helper to create a notification row for a purchase order."""
    supplier_name = po.supplier_profile.company_name if po.supplier_profile else None
    PurchaseOrderNotification.objects.create(
        purchase_order=po,
        supplier_name=supplier_name,
        status=po.status,
        message=f"PO {po.purchase_order_id} {action_label}",
        payment_due_date=po.payment_due_date if po.pay_later else None,
    )


@receiver(post_save, sender=PurchaseOrderItem)
def update_stock_on_purchase_order_item_save(sender, instance, created, **kwargs):
    """
    Updates product stock when a PurchaseOrderItem is saved.
    Also updates the parent PurchaseOrder's status based on item receipt.
    """
    old_quantity_received = 0
    if instance.pk and not created:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            old_quantity_received = old_instance.quantity_received
        except sender.DoesNotExist:
            old_quantity_received = 0

    net_received_change = instance.quantity_received - old_quantity_received

    if net_received_change != 0:
        product_variant = instance.product_variant
        if product_variant:
            product_variant.stock += net_received_change
            product_variant.save()
            movement_type = "IN" if net_received_change > 0 else "OUT"
            StockMovement.objects.create(
                product=product_variant.product,
                product_variant=product_variant,
                movement_type=movement_type,
                quantity=abs(net_received_change),
                reason=f"PO Receipt Adjustment: {instance.purchase_order.purchase_order_id}",
            )

    # Update parent PO status based on receipt progress
    purchase_order = instance.purchase_order
    total_ordered = sum(item.quantity_ordered for item in purchase_order.items.all())
    total_received = sum(item.quantity_received for item in purchase_order.items.all())
    
    if total_received == total_ordered and total_ordered > 0:
        if purchase_order.status != purchase_order.STATUS_RECEIVED:
            purchase_order.status = purchase_order.STATUS_RECEIVED
            purchase_order.received_date = timezone.now()
            purchase_order.save(update_fields=["status", "received_date"])
    elif 0 < total_received < total_ordered:
        if purchase_order.status != purchase_order.STATUS_PARTIALLY_RECEIVED:
            purchase_order.status = purchase_order.STATUS_PARTIALLY_RECEIVED
            purchase_order.save(update_fields=["status"])
    elif total_received == 0 and purchase_order.status not in [
        purchase_order.STATUS_DRAFT,
        purchase_order.STATUS_REQUEST_PENDING,
        purchase_order.STATUS_CANCELLED,
        purchase_order.STATUS_REFUND,  # ← NEW
    ]:
        # Revert to confirmed/in_transit if all items removed
        purchase_order.status = purchase_order.STATUS_CONFIRMED
        purchase_order.received_date = None
        purchase_order.save(update_fields=["status", "received_date"])


@receiver(post_save, sender=PurchaseOrder)
def handle_po_cancellation_and_notifications(sender, instance, created, **kwargs):
    """
    Handles PO status transitions and creates notifications.
    """
    old_status = None
    if instance.pk and not created:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            old_status = old_instance.status
        except sender.DoesNotExist:
            old_status = None

    # ═════════════════════════════════════════════════════════════
    # REFUND LOGIC: Reverse stock when moving to REFUND status
    # ═════════════════════════════════════════════════════════════
    if old_status != instance.status and instance.status == instance.STATUS_REFUND:
        if old_status in [instance.STATUS_RECEIVED, instance.STATUS_PARTIALLY_RECEIVED]:
            for item in instance.items.all():
                if item.quantity_received > 0 and item.product_variant:
                    pv = item.product_variant
                    pv.stock -= item.quantity_received
                    pv.save()
                    StockMovement.objects.create(
                        product=pv.product,
                        product_variant=pv,
                        movement_type="OUT",
                        quantity=item.quantity_received,
                        reason=f"PO Refund/Return: {instance.purchase_order_id}",
                    )
                    # Don't reset quantity_received yet - keep record of what was returned

    # ═════════════════════════════════════════════════════════════
    # CANCELLATION LOGIC: Reverse stock if cancelled after receipt
    # ═════════════════════════════════════════════════════════════
    if old_status != instance.status and instance.status == instance.STATUS_CANCELLED:
        if old_status in [instance.STATUS_RECEIVED, instance.STATUS_PARTIALLY_RECEIVED]:
            for item in instance.items.all():
                if item.quantity_received > 0 and item.product_variant:
                    pv = item.product_variant
                    pv.stock -= item.quantity_received
                    pv.save()
                    StockMovement.objects.create(
                        product=pv.product,
                        product_variant=pv,
                        movement_type="OUT",
                        quantity=item.quantity_received,
                        reason=f"PO Cancellation: {instance.purchase_order_id}",
                    )
                    item.quantity_received = 0
                    item.save(update_fields=["quantity_received"])

    # Create notification
    try:
        _create_po_notification(instance, "created" if created else "updated")
    except Exception as e:
        print(f"PO notification error: {e}")