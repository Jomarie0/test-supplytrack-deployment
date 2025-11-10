# apps/orders/manual_views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q, Sum, Count
from django.utils import timezone
from datetime import timedelta
import json
from apps.inventory.models import Product, StockMovement # Import StockMovement
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from apps.transactions.models import log_audit  # ensure present

from .models import ManualOrder, ManualOrderItem
from .forms import ManualOrderForm, ManualOrderItemFormSet
from apps.users.models import User
from apps.store.models import ProductVariant
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)    

@login_required
def manual_order_management(request):
    """
    Main page for manual order management
    """
    # Get all non-deleted manual orders
    orders = ManualOrder.objects.filter(is_deleted=False).select_related(
        'customer', 'created_by'
    ).prefetch_related('items__product_variant__product').order_by('-order_date')
    
    # Calculate KPIs
    total_orders = orders.count()
    pending_orders = orders.filter(status='Pending').count()
    completed_orders = orders.filter(status='Completed').count()
    
    # Calculate total revenue from completed orders
    total_revenue = sum(order.get_total_cost for order in orders.filter(status='Completed'))
    
    # Status breakdown
    status_counts = orders.values('status').annotate(count=Count('id'))
    
    context = {
        'orders': orders,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'total_revenue': total_revenue,
        'status_counts': status_counts,
        'form': ManualOrderForm(),
        'customers': User.objects.all(),
        'product_variants': ProductVariant.objects.filter(
            is_active=True,
            product__is_deleted=False
        ).select_related('product').order_by('product__name'),
        'payment_methods': ManualOrder.PAYMENT_METHODS,  # Add this
        'status_choices': ManualOrder.STATUS_CHOICES,  # Add this
        'order_source_choices': ManualOrder.ORDER_SOURCE_CHOICES,  # Add this
    }
    
    return render(request, 'orders/manual_order_management/manual_order_management.html', context)
@login_required
@transaction.atomic
def create_manual_order(request):
    """
    Create manual order with immediate stock deduction
    """
    if request.method == 'POST':
        form = ManualOrderForm(request.POST)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Parse items from POST data
                    items_data = json.loads(request.POST.get('items_json', '[]'))
                    
                    if not items_data:
                        messages.error(request, 'Please add at least one item to the order.')
                        return redirect('orders:manual_order_management')
                    
                    # STEP 1: Validate all stock BEFORE creating order
                    products_to_update = []
                    validation_errors = []
                    
                    for item_data in items_data:
                        variant_id = item_data.get('product_variant_id')
                        quantity = int(item_data.get('quantity', 1))
                        price = item_data.get('price_at_order')
                        
                        # Get variant and lock product row
                        variant = ProductVariant.objects.select_related('product').get(id=variant_id)
                        product = Product.objects.select_for_update().get(pk=variant.product.pk)
                        
                        # Validate stock availability
                        if product.stock_quantity < quantity:
                            validation_errors.append(
                                f"{product.name}: Required {quantity}, Available {product.stock_quantity}"
                            )
                        else:
                            # Use variant price or product price if not specified
                            final_price = price if price else (variant.price or product.price or Decimal('0.00'))
                            
                            products_to_update.append({
                                'product': product,
                                'variant': variant,
                                'quantity': quantity,
                                'price': final_price
                            })
                    
                    # Abort if any validation errors
                    if validation_errors:
                        for error in validation_errors:
                            messages.error(request, f"Insufficient stock: {error}")
                        return redirect('orders:manual_order_management')
                    
                    # STEP 2: Create the manual order
                    order = form.save(commit=False)
                    order.created_by = request.user
                    order.status = 'Pending'  # Start as Pending
                    order.save()
                    
                    # STEP 3: Create order items AND deduct stock immediately
                    for item_data in products_to_update:
                        product = item_data['product']
                        variant = item_data['variant']
                        quantity = item_data['quantity']
                        price = item_data['price']
                        
                        # Create order item
                        ManualOrderItem.objects.create(
                            order=order,
                            product_variant=variant,
                            quantity=quantity,
                            price_at_order=price
                        )
                        
                        # DEDUCT STOCK IMMEDIATELY
                        product.stock_quantity -= quantity
                        product.save()
                        
                        # Record stock movement
                        StockMovement.objects.create(
                            product=product,
                            movement_type='OUT',
                            quantity=quantity,
                            # reference=f"Manual Order {order.manual_order_id}",
                            # notes=f"Stock reserved - Manual order created (Pending)"
                        )
                        
                        logger.info(
                            f"Deducted {quantity} units of {product.name} for Manual Order {order.manual_order_id}"
                        )
                    
                    # Mark stock as deducted
                    order.stock_deducted = True
                    order.stock_deducted_at = timezone.now()
                    order.save()
                    
                    messages.success(
                        request,
                        f'Manual order {order.manual_order_id} created successfully! '
                        f'Stock has been reserved and deducted from inventory.'
                    )
                    return redirect('orders:manual_order_management')
                    
            except ProductVariant.DoesNotExist:
                messages.error(request, 'One or more product variants are no longer available.')
                return redirect('orders:manual_order_management')
            
            except Product.DoesNotExist:
                messages.error(request, 'One or more products are no longer available.')
                return redirect('orders:manual_order_management')
            
            except ValueError as e:
                messages.error(request, str(e))
                return redirect('orders:manual_order_management')
            
            except Exception as e:
                logger.error(f"Manual order creation error: {str(e)}")
                messages.error(request, f'Error creating order: {str(e)}')
                return redirect('orders:manual_order_management')
        else:
            messages.error(request, 'Please correct the errors below.')
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    
    return redirect('orders:manual_order_management')

@login_required
def manual_order_details_api(request, order_id):
    """
    API endpoint to get manual order details
    """
    try:
        order = ManualOrder.objects.select_related('customer', 'created_by').prefetch_related(
            'items__product_variant__product'
        ).get(id=order_id, is_deleted=False)
        
        # Prepare order items
        items = []
        for item in order.items.all():
            items.append({
                'id': item.id,
                'product_name': item.product_variant.product.name,
                'variant_sku': item.product_variant.sku,
                'quantity': item.quantity,
                'price': float(item.price_at_order),
                'total': float(item.item_total)
            })
        
        return JsonResponse({
            'id': order.id,
            'manual_order_id': order.manual_order_id,
            'customer_name': order.get_customer_display(),
            'customer_email': order.customer_email or 'N/A',
            'customer_phone': order.customer_phone or 'N/A',
            'order_source': order.get_order_source_display(),
            'payment_method': order.get_payment_method_display(),
            'status': order.status,
            'order_date': order.order_date.isoformat(),
            'expected_delivery_date': order.expected_delivery_date.isoformat() if order.expected_delivery_date else None,
            'shipping_address': order.shipping_address,
            'billing_address': order.billing_address,
            'notes': order.notes or '',
            'total_cost': float(order.get_total_cost),
            'items': items,
            'created_by': order.created_by.username if order.created_by else 'Unknown',
            'created_at': order.created_at.isoformat(),
            'payment_status': order.payment_status,
            'payment_verified_at': order.payment_verified_at.isoformat() if order.payment_verified_at else None,
            'gcash_reference_image_url': order.gcash_reference_image.url if order.gcash_reference_image else None,
            
        })
        
    except ManualOrder.DoesNotExist:
        return JsonResponse({'error': 'Order not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Unexpected error: {str(e)}'}, status=500)


@login_required
@csrf_exempt
def update_manual_order(request, order_id):
    """
    Update manual order status
    """
    if request.method == 'POST':
        try:
            order = ManualOrder.objects.get(id=order_id, is_deleted=False)
            data = json.loads(request.body)
            new_status = data.get('status')
            
            if new_status in dict(ManualOrder.STATUS_CHOICES):
                order.status = new_status
                order.save()
                return JsonResponse({'success': True, 'message': f'Order status updated to {new_status}'})
            else:
                return JsonResponse({'success': False, 'error': 'Invalid status'}, status=400)
                
        except ManualOrder.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Order not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


# apps/orders/manual_views.py

# apps/orders/manual_views.py


@login_required
@require_POST
def delete_manual_orders(request):
    """
    Deletes (soft-delete) selected manual orders and restores stock for reserved items.
    Expects JSON payload: { "ids": [1, 2, 3] }
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    order_ids = data.get('ids', [])
    if not isinstance(order_ids, list) or not order_ids:
        return JsonResponse({"success": False, "error": "No order IDs provided."}, status=400)

    try:
        with transaction.atomic():
            # Lock selected orders for update to avoid race conditions
            # prefetch related item -> variant -> product to avoid extra queries and avoid selecting non-existent fields
            orders = ManualOrder.objects.select_for_update().filter(id__in=order_ids, is_deleted=False) \
                .prefetch_related('items__product_variant__product')

            if not orders.exists():
                return JsonResponse({"success": False, "error": "Orders not found."}, status=404)

            deleted_count = 0

            for order in orders:
                # Restore stock only if stock was previously deducted for this order
                per_order_product_changes = {}
                if getattr(order, 'stock_deducted', False):
                     # ManualOrderItem has product_variant; it may not have a direct 'product' FK.
                     # Use select_related on product_variant__product (prefetched above) to access the product safely.
                    for item in order.items.select_related('product_variant__product'):
                        qty = int(getattr(item, 'quantity', 0) or 0)
                        if qty <= 0:
                             continue

                        product = None
                         # Prefer product via variant.product
                        if getattr(item, 'product_variant', None) and getattr(item.product_variant, 'product', None):
                             product = item.product_variant.product
                        elif getattr(item, 'product', None):
                             product = item.product

                        if not product:
                            continue
                        
                        before_qty = product.stock_quantity or 0
                         # Restore stock quantity
                        product.stock_quantity = (product.stock_quantity or 0) + qty
                        product.save(update_fields=['stock_quantity'])

                        after_qty = product.stock_quantity or 0
                        # record change for audit
                        per_order_product_changes[str(product.id)] = {'product': str(product), 'before': before_qty, 'after': after_qty, 'delta': after_qty - before_qty}

                         # Create a StockMovement record (best-effort)
                        try:
                             StockMovement.objects.create(
                                 product=product,
                                 movement_type='IN',
                                 quantity=qty,
                                 notes=f"Restored from manual order {getattr(order, 'manual_order_id', order.id)}"
                             )
                        except Exception:
                             # do not block deletion if stock movement creation fails
                            pass

                # Soft-delete if supported, otherwise hard delete
                if hasattr(order, 'is_deleted'):
                    order.is_deleted = True
                    order.save(update_fields=['is_deleted'])
                else:
                    order.delete()

                # schedule audit per-order after commit
                def _log_deleted(o=order, prod_changes=per_order_product_changes):
                    try:
                        changes = {'deleted': True}
                        if prod_changes:
                            changes['restored_stock'] = prod_changes
                        log_audit(user=request.user, action='delete', instance=o, changes=changes, request=request)
                    except Exception:
                        pass
                transaction.on_commit(_log_deleted)

                deleted_count += 1

        return JsonResponse({"success": True, "deleted_count": deleted_count})

    except Exception as e:
        logger.exception("Error deleting manual orders")
        return JsonResponse({"success": False, "error": str(e)}, status=500)