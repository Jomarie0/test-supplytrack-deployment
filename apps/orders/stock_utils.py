# apps/orders/stock_utils.py
# Create this new file for reusable stock management functions

from django.db import transaction
from apps.inventory.models import Product, StockMovement
import logging

logger = logging.getLogger(__name__)


class InsufficientStockError(Exception):
    """Raised when there's not enough stock to fulfill an order"""

    pass


@transaction.atomic
def validate_and_reserve_stock(items_list, order_reference):
    """
    Validate stock availability and reserve (deduct) stock for an order.

    Args:
        items_list: List of dicts with keys: 'product_id', 'quantity', 'variant'
        order_reference: String reference for the order (e.g., "Order ORD12345")

    Returns:
        List of updated product instances

    Raises:
        InsufficientStockError: If any product has insufficient stock
    """
    products_updated = []
    validation_errors = []

    # Lock all products first to prevent race conditions
    product_ids = [item["product_id"] for item in items_list]
    locked_products = {
        p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)
    }

    # Validate all items before deducting any stock
    for item in items_list:
        product = locked_products.get(item["product_id"])
        quantity = item["quantity"]

        if not product:
            validation_errors.append(f"Product ID {item['product_id']} not found")
            continue

        if product.stock_quantity < quantity:
            validation_errors.append(
                f"{product.name}: Required {quantity}, Available {product.stock_quantity}"
            )
        else:
            products_updated.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "variant": item.get("variant"),
                }
            )

    # If any validation errors, raise exception
    if validation_errors:
        raise InsufficientStockError("; ".join(validation_errors))

    # All validations passed - now deduct stock
    for item in products_updated:
        product = item["product"]
        quantity = item["quantity"]

        product.stock_quantity -= quantity
        product.save()

        StockMovement.objects.create(
            product=product,
            movement_type="OUT",
            quantity=quantity,
            reference=order_reference,
            notes="Stock reserved at order creation",
        )

        logger.info(
            f"Reserved {quantity} units of {product.name} for {order_reference}"
        )

    return products_updated


@transaction.atomic
def restore_stock(items_list, order_reference, reason="Order canceled"):
    """
    Restore stock to inventory when an order is canceled/returned.

    Args:
        items_list: List of dicts with keys: 'product_id', 'quantity'
        order_reference: String reference for the order
        reason: Reason for stock restoration
    """
    product_ids = [item["product_id"] for item in items_list]
    locked_products = {
        p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)
    }

    for item in items_list:
        product = locked_products.get(item["product_id"])
        quantity = item["quantity"]

        if product:
            product.stock_quantity += quantity
            product.save()

            StockMovement.objects.create(
                product=product,
                movement_type="IN",
                quantity=quantity,
                reference=order_reference,
                notes=reason,
            )

            logger.info(
                f"Restored {quantity} units of {product.name} for {order_reference}"
            )


def check_stock_availability(product_id, required_quantity):
    """
    Check if a product has sufficient stock without locking.
    Useful for quick availability checks in UI.

    Args:
        product_id: Product ID to check
        required_quantity: Quantity needed

    Returns:
        tuple: (is_available: bool, available_quantity: int)
    """
    try:
        product = Product.objects.get(id=product_id)
        is_available = product.stock_quantity >= required_quantity
        return is_available, product.stock_quantity
    except Product.DoesNotExist:
        return False, 0


def get_available_stock(product_id):
    """Get current available stock for a product"""
    try:
        product = Product.objects.get(id=product_id)
        return product.stock_quantity
    except Product.DoesNotExist:
        return 0
