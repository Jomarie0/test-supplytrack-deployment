# apps/orders/signals.py
"""
BIDIRECTIONAL ORDER ↔ DELIVERY STATUS SYNCHRONIZATION
=====================================================

This signal handles:
1. Order status changes → Delivery status updates
2. Delivery status changes → Order status updates (BIDIRECTIONAL)
3. Stock deduction/restoration based on status
4. Automatic delivery creation for new orders
5. Reprocessing of failed/canceled orders

Status Mappings:
---------------
Order Status        → Delivery Status
- Pending           → pending_dispatch
- Processing        → pending_dispatch
- Shipped           → out_for_delivery
- Completed         → delivered
- Returned          → failed
- Canceled          → failed

Delivery Status     → Order Status
- pending_dispatch  → Processing (when order is Pending)
- out_for_delivery  → Shipped
- delivered         → Completed
- failed            → Returned
"""

import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.db import transaction
from django.utils import timezone
from .models import Order, ManualOrder
from apps.inventory.models import Product, StockMovement

logger = logging.getLogger(__name__)

# Cache for tracking previous statuses
_previous_order_status = {}
_previous_manual_order_status = {}
_previous_delivery_status = {}

# Flag to prevent infinite recursion during bidirectional sync
_sync_in_progress = set()


# ==================== ORDER STATUS TRACKING ====================

@receiver(pre_save, sender=Order)
def store_initial_order_status(sender, instance, **kwargs):
    """Store previous status to detect changes"""
    if instance.pk:
        try:
            _previous_order_status[instance.pk] = Order.objects.get(pk=instance.pk).status
        except Order.DoesNotExist:
            pass


# ==================== ORDER → DELIVERY SYNC ====================

@receiver(post_save, sender=Order)
def sync_order_to_delivery(sender, instance, created, **kwargs):
    """
    PART 1: Order changes → Update Delivery
    - Creates delivery for new orders
    - Syncs delivery status when order status changes
    - Handles reprocessing of failed orders
    """
    
    # Import here to avoid circular dependency
    from apps.delivery.models import Delivery
    
    # Prevent infinite recursion
    sync_key = f"order_{instance.pk}"
    if sync_key in _sync_in_progress:
        return
    
    try:
        _sync_in_progress.add(sync_key)
        
        # Get or create delivery
        delivery, delivery_created = Delivery.objects.get_or_create(
            order=instance,
            defaults={'delivery_status': Delivery.PENDING_DISPATCH}
        )
        
        if delivery_created:
            logger.info(f"✅ Delivery #{delivery.id} created for Order {instance.order_id}")
            return  # No further sync needed for new deliveries
        
        # Status mapping: Order → Delivery
        status_mapping = {
            "Pending": Delivery.PENDING_DISPATCH,
            "Processing": Delivery.PENDING_DISPATCH,
            "Shipped": Delivery.OUT_FOR_DELIVERY,
            "Completed": Delivery.DELIVERED,
            "Returned": Delivery.FAILED,
            "Canceled": Delivery.FAILED,
        }
        
        expected_delivery_status = status_mapping.get(instance.status)
        current_delivery_status = delivery.delivery_status
        
        # Handle reprocessing: If order goes back to Pending/Processing from Failed
        if instance.status in ["Pending", "Processing"] and current_delivery_status == Delivery.FAILED:
            delivery.delivery_status = Delivery.PENDING_DISPATCH
            delivery.save(update_fields=['delivery_status'])
            logger.info(f"🔄 Delivery #{delivery.id} reset to PENDING_DISPATCH for reprocessed Order {instance.order_id}")
        
        # Normal sync: Update delivery if status doesn't match
        elif expected_delivery_status and current_delivery_status != expected_delivery_status:
            delivery.delivery_status = expected_delivery_status
            
            # Set delivered_at timestamp when marked as delivered
            if expected_delivery_status == Delivery.DELIVERED and not delivery.delivered_at:
                delivery.delivered_at = timezone.now()
                delivery.save(update_fields=['delivery_status', 'delivered_at'])
            else:
                delivery.save(update_fields=['delivery_status'])
            
            logger.info(f"🔄 Delivery #{delivery.id} synced to {expected_delivery_status} (Order {instance.order_id} → {instance.status})")
    
    except Exception as e:
        logger.error(f"❌ Error syncing Order → Delivery for {instance.order_id}: {str(e)}")
    
    finally:
        _sync_in_progress.discard(sync_key)


# ==================== ORDER STOCK MANAGEMENT ====================

@receiver(post_save, sender=Order)
def handle_order_stock_changes(sender, instance, created, **kwargs):
    """
    PART 2: Stock deduction/restoration based on order status
    - Restores stock when order is Canceled or Returned
    - Re-deducts stock when order is reactivated
    - Stock is initially deducted in checkout_view
    """
    
    if created:
        return  # Stock is handled in checkout_view for new orders
    
    current_status = instance.status
    previous_status = _previous_order_status.pop(instance.pk, None)
    
    if not previous_status or previous_status == current_status:
        return  # No status change
    
    logger.info(f"Order {instance.order_id} status: {previous_status} → {current_status}")
    
    # ========== RESTORE STOCK (Canceled/Returned) ==========
    if current_status in ["Canceled", "Returned"] and previous_status not in ["Canceled", "Returned"]:
        if instance.stock_deducted and not instance.stock_restored:
            with transaction.atomic():
                for item in instance.items.select_related("product_variant__product").all():
                    product = Product.objects.select_for_update().get(pk=item.product_variant.product.pk)
                    product.stock_quantity += item.quantity
                    product.save()
                    
                    StockMovement.objects.create(
                        product=product,
                        movement_type="IN",
                        quantity=item.quantity,
                    )
                
                instance.stock_restored = True
                instance.stock_restored_at = timezone.now()
                instance.save(update_fields=["stock_restored", "stock_restored_at"])
            
            logger.info(f"✅ Stock restored for Order {instance.order_id} ({current_status})")
    
    # ========== RE-DEDUCT STOCK (Reactivation) ==========
    elif previous_status in ["Canceled", "Returned"] and current_status in ["Pending", "Processing", "Shipped", "Completed"]:
        if instance.stock_restored:
            with transaction.atomic():
                insufficient_stock_errors = []
                
                for item in instance.items.select_related("product_variant__product").all():
                    product = Product.objects.select_for_update().get(pk=item.product_variant.product.pk)
                    
                    if product.stock_quantity >= item.quantity:
                        product.stock_quantity -= item.quantity
                        product.save()
                        
                        StockMovement.objects.create(
                            product=product,
                            movement_type="OUT",
                            quantity=item.quantity,
                        )
                    else:
                        insufficient_stock_errors.append(
                            f"{product.name}: Need {item.quantity}, only {product.stock_quantity} available"
                        )
                
                if insufficient_stock_errors:
                    # Revert status change
                    instance.status = previous_status
                    instance.save(update_fields=["status"])
                    error_msg = "; ".join(insufficient_stock_errors)
                    logger.error(f"❌ Cannot reactivate Order {instance.order_id}: {error_msg}")
                    raise ValueError(f"Insufficient stock: {error_msg}")
                
                instance.stock_restored = False
                instance.stock_restored_at = None
                instance.stock_deducted_at = timezone.now()
                instance.save(update_fields=["stock_restored", "stock_restored_at", "stock_deducted_at"])
            
            logger.info(f"✅ Stock re-deducted for reactivated Order {instance.order_id}")


# ==================== DELIVERY → ORDER SYNC ====================

@receiver(pre_save, sender='delivery.Delivery')
def store_initial_delivery_status(sender, instance, **kwargs):
    """Store previous delivery status to detect changes"""
    if instance.pk:
        try:
            _previous_delivery_status[instance.pk] = sender.objects.get(pk=instance.pk).delivery_status
        except sender.DoesNotExist:
            pass


@receiver(post_save, sender='delivery.Delivery')
def sync_delivery_to_order(sender, instance, created, **kwargs):
    """
    PART 3: Delivery changes → Update Order (BIDIRECTIONAL SYNC)
    
    This is the NEW signal that makes the sync bidirectional!
    
    Delivery Status → Order Status:
    - pending_dispatch → Processing (if order is Pending)
    - out_for_delivery → Shipped
    - delivered        → Completed
    - failed           → Returned
    """
    
    if created:
        return  # New deliveries are handled by order signal
    
    # Prevent infinite recursion
    sync_key = f"delivery_{instance.pk}"
    if sync_key in _sync_in_progress:
        return
    
    try:
        _sync_in_progress.add(sync_key)
        
        order = instance.order
        current_delivery_status = instance.delivery_status
        previous_delivery_status = _previous_delivery_status.pop(instance.pk, None)
        
        if not previous_delivery_status or previous_delivery_status == current_delivery_status:
            return  # No status change
        
        logger.info(f"Delivery #{instance.id} status: {previous_delivery_status} → {current_delivery_status}")
        
        # Status mapping: Delivery → Order
        from apps.delivery.models import Delivery
        
        status_mapping = {
            Delivery.PENDING_DISPATCH: "Processing",  # Ready for dispatch
            Delivery.OUT_FOR_DELIVERY: "Shipped",     # On the way
            Delivery.DELIVERED: "Completed",          # Successfully delivered
            Delivery.FAILED: "Returned",              # Delivery failed
        }
        
        expected_order_status = status_mapping.get(current_delivery_status)
        current_order_status = order.status
        
        # Only update if order status needs to change
        if expected_order_status and current_order_status != expected_order_status:
            # Special case: Don't override Canceled orders
            if current_order_status == "Canceled":
                logger.info(f"ℹ️ Skipping sync for canceled Order {order.order_id}")
                return
            
            order.status = expected_order_status
            order.save(update_fields=['status'])
            logger.info(f"🔄 Order {order.order_id} synced to {expected_order_status} (Delivery #{instance.id} → {current_delivery_status})")
    
    except Exception as e:
        logger.error(f"❌ Error syncing Delivery → Order for Delivery #{instance.pk}: {str(e)}")
    
    finally:
        _sync_in_progress.discard(sync_key)


# ==================== MANUAL ORDERS ====================

@receiver(pre_save, sender=ManualOrder)
def store_initial_manual_order_status(sender, instance, **kwargs):
    """Store previous manual order status"""
    if instance.pk:
        try:
            _previous_manual_order_status[instance.pk] = ManualOrder.objects.get(pk=instance.pk).status
        except ManualOrder.DoesNotExist:
            pass


@receiver(post_save, sender=ManualOrder)
def handle_manual_order_stock_changes(sender, instance, created, **kwargs):
    """
    Stock management for manual orders (same logic as regular orders)
    """
    
    if created:
        logger.info(f"New Manual Order {instance.manual_order_id} created - stock deducted in view")
        return
    
    current_status = instance.status
    previous_status = _previous_manual_order_status.pop(instance.pk, None)
    
    if not previous_status or previous_status == current_status:
        return
    
    logger.info(f"Manual Order {instance.manual_order_id} status: {previous_status} → {current_status}")
    
    # ========== RESTORE STOCK ==========
    if current_status in ["Canceled", "Returned"] and previous_status not in ["Canceled", "Returned"]:
        if instance.stock_deducted and not instance.stock_restored:
            with transaction.atomic():
                for item in instance.items.select_related("product_variant__product").all():
                    product = Product.objects.select_for_update().get(pk=item.product_variant.product.pk)
                    product.stock_quantity += item.quantity
                    product.save()
                    
                    StockMovement.objects.create(
                        product=product,
                        movement_type="IN",
                        quantity=item.quantity,
                    )
                
                instance.stock_restored = True
                instance.stock_restored_at = timezone.now()
                instance.save(update_fields=["stock_restored", "stock_restored_at"])
            
            logger.info(f"✅ Stock restored for Manual Order {instance.manual_order_id}")
    
    # ========== RE-DEDUCT STOCK ==========
    elif previous_status in ["Canceled", "Returned"] and current_status in ["Pending", "Processing", "Shipped", "Completed"]:
        if instance.stock_restored:
            with transaction.atomic():
                insufficient_stock_errors = []
                
                for item in instance.items.select_related("product_variant__product").all():
                    product = Product.objects.select_for_update().get(pk=item.product_variant.product.pk)
                    
                    if product.stock_quantity >= item.quantity:
                        product.stock_quantity -= item.quantity
                        product.save()
                        
                        StockMovement.objects.create(
                            product=product,
                            movement_type="OUT",
                            quantity=item.quantity,
                        )
                    else:
                        insufficient_stock_errors.append(
                            f"{product.name}: Need {item.quantity}, only {product.stock_quantity} available"
                        )
                
                if insufficient_stock_errors:
                    instance.status = previous_status
                    instance.save(update_fields=["status"])
                    error_msg = "; ".join(insufficient_stock_errors)
                    logger.error(f"❌ Cannot reactivate Manual Order {instance.manual_order_id}: {error_msg}")
                    raise ValueError(f"Insufficient stock: {error_msg}")
                
                instance.stock_restored = False
                instance.stock_restored_at = None
                instance.stock_deducted_at = timezone.now()
                instance.save(update_fields=["stock_restored", "stock_restored_at", "stock_deducted_at"])
            
            logger.info(f"✅ Stock re-deducted for Manual Order {instance.manual_order_id}")