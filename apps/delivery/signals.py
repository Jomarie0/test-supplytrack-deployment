# apps/delivery/signals.py
"""
DELIVERY SIGNALS - Handles Delivery-specific logic
===================================================

This file works together with apps/orders/signals.py to provide
complete bidirectional synchronization between Orders and Deliveries.

Signal Responsibilities:
------------------------
1. orders/signals.py:
   - Order → Delivery sync (status mapping)
   - Stock management for orders
   - Delivery creation for new orders

2. delivery/signals.py (THIS FILE):
   - Delivery → Order sync (bidirectional)
   - Delivery-specific business logic
   - Email notifications for delivery updates

The signals in orders/signals.py handle the Delivery model using
the 'delivery.Delivery' string reference to avoid circular imports.
"""

import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Delivery

logger = logging.getLogger(__name__)

# Cache for previous delivery status
_previous_delivery_status = {}

# Prevent infinite recursion during bidirectional sync
_sync_in_progress = set()


@receiver(pre_save, sender=Delivery)
def store_previous_delivery_status(sender, instance, **kwargs):
    """
    Capture the previous delivery status before save.
    This allows us to detect status changes in post_save.
    """
    if instance.pk:
        try:
            _previous_delivery_status[instance.pk] = Delivery.objects.get(
                pk=instance.pk
            ).delivery_status
        except Delivery.DoesNotExist:
            pass


@receiver(post_save, sender=Delivery)
def sync_delivery_status_to_order(sender, instance, created, **kwargs):
    """
    BIDIRECTIONAL SYNC: Delivery status changes → Update Order status
    
    This is the key signal that makes your system bidirectional!
    When delivery personnel update the delivery status, the order
    status automatically updates.
    
    Mapping:
    --------
    Delivery Status       → Order Status
    - pending_dispatch    → Processing
    - out_for_delivery    → Shipped
    - delivered           → Completed
    - failed              → Returned
    """
    
    if created:
        logger.info(f"✅ New Delivery #{instance.id} created for Order {instance.order.order_id}")
        return  # New deliveries don't need sync (order signal handles it)
    
    # Prevent infinite recursion
    sync_key = f"delivery_{instance.pk}"
    if sync_key in _sync_in_progress:
        return
    
    try:
        _sync_in_progress.add(sync_key)
        
        order = instance.order
        current_delivery_status = instance.delivery_status
        previous_delivery_status = _previous_delivery_status.pop(instance.pk, None)
        
        # Only proceed if status actually changed
        if not previous_delivery_status or previous_delivery_status == current_delivery_status:
            return
        
        logger.info(
            f"Delivery #{instance.id} status changed: "
            f"{previous_delivery_status} → {current_delivery_status}"
        )
        
        # Status mapping: Delivery → Order
        status_mapping = {
            Delivery.PENDING_DISPATCH: "Processing",
            Delivery.OUT_FOR_DELIVERY: "Shipped",
            Delivery.DELIVERED: "Completed",
            Delivery.FAILED: "Returned",
        }
        
        expected_order_status = status_mapping.get(current_delivery_status)
        current_order_status = order.status
        
        # Update order status if it needs to change
        if expected_order_status and current_order_status != expected_order_status:
            
            # Don't override canceled orders
            if current_order_status == "Canceled":
                logger.info(
                    f"ℹ️ Skipping sync for canceled Order {order.order_id} "
                    f"(Delivery status: {current_delivery_status})"
                )
                return
            
            # Update the order
            order.status = expected_order_status
            order.save()
            if current_delivery_status == Delivery.DELIVERED:
                try:
                    # Only apply to COD orders that are unpaid
                    if order.payment_method == "COD" and order.payment_status.lower() == "unpaid":
                        order.payment_status = "paid"
                        order.payment_verified_at = timezone.now()
                        order.save(update_fields=["payment_status", "payment_verified_at"])
                        logger.info(f"💰 Order {order.order_id} marked as PAID (COD auto-update after delivery).")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to auto-mark order {order.order_id} as paid: {e}")

            logger.info(
                f"🔄 Order {order.order_id} synced to {expected_order_status} "
                f"(Delivery #{instance.id} marked as {current_delivery_status})"
            )
            
            # Send notification email (optional)
            try:
                send_delivery_status_email(instance)
            except Exception as e:
                logger.warning(f"Failed to send delivery notification email: {e}")
        
        else:
            logger.info(
                f"ℹ️ Order {order.order_id} already in correct status "
                f"({current_order_status})"
            )
    
    except Exception as e:
        logger.error(
            f"❌ Error syncing Delivery → Order for Delivery #{instance.pk}: {str(e)}"
        )
    
    finally:
        _sync_in_progress.discard(sync_key)


@receiver(post_save, sender=Delivery)
def auto_set_delivered_timestamp(sender, instance, created, **kwargs):
    """
    Automatically set delivered_at timestamp when status becomes DELIVERED.
    This ensures the timestamp is always set, even if the view forgets to do it.
    """
    
    if instance.delivery_status == Delivery.DELIVERED and not instance.delivered_at:
        # Avoid triggering another save signal recursion
        if f"timestamp_{instance.pk}" not in _sync_in_progress:
            _sync_in_progress.add(f"timestamp_{instance.pk}")
            try:
                instance.delivered_at = timezone.now()
                instance.save(update_fields=['delivered_at'])
                logger.info(f"✅ Auto-set delivered_at for Delivery #{instance.id}")
            finally:
                _sync_in_progress.discard(f"timestamp_{instance.pk}")


def send_delivery_status_email(delivery):
    """
    Send email notification to customer about delivery status update.
    Safe fallback - won't break if email fails.
    """
    try:
        from django.core.mail import send_mail
        from django.conf import settings
        
        customer = delivery.order.customer
        if not customer or not customer.email:
            return
        
        status_messages = {
            Delivery.PENDING_DISPATCH: "Your order is being prepared for dispatch",
            Delivery.OUT_FOR_DELIVERY: "Your order is out for delivery",
            Delivery.DELIVERED: "Your order has been delivered successfully",
            Delivery.FAILED: "Delivery attempt failed - we'll contact you shortly",
        }
        
        subject = f"Delivery Update for Order {delivery.order.order_id}"
        message = (
            f"Hello {customer.get_full_name() or customer.username},\n\n"
            f"{status_messages.get(delivery.delivery_status, 'Delivery status updated')}.\n\n"
            f"Order ID: {delivery.order.order_id}\n"
            f"Status: {delivery.get_delivery_status_display()}\n\n"
            f"Thank you for your order!\n"
            f"- SupplyTrack Team"
        )
        
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [customer.email],
            fail_silently=True,
        )
        
        logger.info(f"📧 Delivery notification sent to {customer.email}")
    
    except Exception as e:
        logger.warning(f"Failed to send delivery email: {e}")
        # Don't raise - email failure shouldn't break delivery updates