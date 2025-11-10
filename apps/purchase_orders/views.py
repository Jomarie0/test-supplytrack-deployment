# apps/purchasing/views.py

import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.utils.timezone import now as tz_now
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Prefetch
from django.db import transaction
from django.urls import reverse
from decimal import Decimal

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderNotification
from .forms import PurchaseOrderForm, PurchaseOrderItemFormSet,POConfirmationForm
from apps.users.models import SupplierProfile
from apps.inventory.models import Product
from apps.store.models import ProductVariant
from django.db.models import Count
from apps.transactions.models import log_audit
from apps.transactions.utils import compute_instance_diff  # added
from django.db import transaction  # already present

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from .models import PurchaseOrder, PurchaseOrderItem
from apps.transactions.models import log_audit

# --------------------------------------
# Email function
# --------------------------------------
def send_purchase_order_email(purchase_order):
    """Send email when PO status is request_pending."""
    if purchase_order.status != PurchaseOrder.STATUS_REQUEST_PENDING:
        return

    supplier_profile = purchase_order.supplier_profile
    supplier_email = supplier_profile.user.email if supplier_profile else None
    if not supplier_email:
        print(f"No email found for supplier {supplier_profile.company_name if supplier_profile else 'Unknown'}")
        return

    subject = f"Purchase Order {purchase_order.purchase_order_id} - PRICE QUOTATION REQUEST"
    
    # Build items list for email
    items_text = ""
    for item in purchase_order.items.all():
        item_name = item.product_variant.product.name if item.product_variant else item.product_name_text
        items_text += f"- {item.quantity_ordered}x {item_name}\n"
    
    message = (
        f"Dear {supplier_profile.company_name},\n\n"
        f"We have a new Purchase Order ({purchase_order.purchase_order_id}) requiring your pricing and confirmation.\n\n"
        f"**ACTION REQUIRED:** Please log into your supplier dashboard to view the requested products/quantities and submit your unit prices.\n\n"
        f"ITEMS REQUESTED:\n{items_text}\n"
        f"Expected Delivery Date: {purchase_order.expected_delivery_date or 'TBD'}\n"
        f"Notes: {purchase_order.notes or 'No additional notes.'}\n\n"
        f"Best regards,\nSupplyTrack Team"
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[supplier_email],
        fail_silently=False,
    )


# --------------------------------------
# List & manage POs
# --------------------------------------
@login_required
def purchase_order_list(request):
    """
    Handles displaying the list of Purchase Orders and the creation of a new PO Draft.
    """
    queryset = PurchaseOrder.objects.all().order_by('-order_date')
    
    # 1. Check for the 'status' query parameter
    status_filter = request.GET.get('status')
    # Filter by status if provided
    purchase_orders = PurchaseOrder.objects.filter(is_deleted=False)
    if status_filter:
        # 2. Apply the filter to the queryset
        # Note: 'status_filter' should match the value in your URL (e.g., 'draft', 'request_pending')
        queryset = queryset.filter(status=status_filter)
        
    # Optional: Calculate counts for the filter buttons
    pending_count = PurchaseOrder.objects.filter(status='request_pending').count()
    draft_count = PurchaseOrder.objects.filter(status='draft').count()
  
    status_filter = request.GET.get('status')
    
    if status_filter:
        # Filter the main queryset based on the status parameter
        purchase_orders = purchase_orders.filter(status=status_filter).order_by('-order_date')
    else:
        # Default view (e.g., show all, or exclude DRAFT if that's your policy)
        # We'll just show all active, non-deleted orders by default
        purchase_orders = purchase_orders.order_by('-order_date')
    
    purchase_orders = purchase_orders.order_by('-order_date')
    
    # Get counts for badges
    total_count = PurchaseOrder.objects.filter(
        is_deleted=False, 
    ).count()
    pending_count = PurchaseOrder.objects.filter(
        is_deleted=False, 
        status=PurchaseOrder.STATUS_REQUEST_PENDING
    ).count()
    draft_count = PurchaseOrder.objects.filter(
        is_deleted=False, 
        status=PurchaseOrder.STATUS_DRAFT
    ).count()
    supplier_priced_count = PurchaseOrder.objects.filter(
        is_deleted=False, 
        status=PurchaseOrder.STATUS_SUPPLIER_PRICED
    ).count()
    confirmed_count = PurchaseOrder.objects.filter(
        is_deleted=False, 
        status=PurchaseOrder.STATUS_CONFIRMED
    ).count()
    received_count = PurchaseOrder.objects.filter(
        is_deleted=False, 
        status=PurchaseOrder.STATUS_RECEIVED
    ).count()
    
    recent_notifications = PurchaseOrderNotification.objects.select_related(
        'purchase_order'
    ).order_by('-created_at')[:10]
    
    form = PurchaseOrderForm()
    
    # --- POST: Create New Draft ---
    if request.method == 'POST':
        form = PurchaseOrderForm(request.POST)

        if form.is_valid():
            with transaction.atomic():
                purchase_order = form.save(commit=False)
                purchase_order.created_by = request.user
                purchase_order.status = PurchaseOrder.STATUS_DRAFT
                # Expected delivery date will be set by supplier when they submit pricing
                purchase_order.save()
                
                messages.success(
                    request, 
                    f"Draft Purchase Order {purchase_order.purchase_order_id} created. "
                    "Please add items on the detail page."
                )

                # Audit: PO created
                try:
                    log_audit(
                        user=request.user,
                        action='create',
                        instance=purchase_order,
                        changes={'status': [None, PurchaseOrder.STATUS_DRAFT]},
                        request=request,
                        extra={'purchase_order_id': purchase_order.purchase_order_id}
                    )
                except Exception:
                    pass
            
            return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order.purchase_order_id)
        else:
            messages.error(request, "Error creating Purchase Order draft. Please check the form.")

    context = {
        'purchase_orders': purchase_orders,
        'form': form,
        'recent_notifications': recent_notifications,
        'pending_count': pending_count,
        'purchase_orders': queryset,
        'pending_count': pending_count,
        'draft_count': draft_count,
        'total_count': total_count,
        'supplier_priced_count': supplier_priced_count,
        'confirmed_count': confirmed_count,
        'received_count': received_count,
        
    }
    return render(request, 'purchase_orders/purchase_order_list.html', context)


# --------------------------------------
# Staff/Manager PO Detail View
# --------------------------------------
@login_required
def purchase_order_detail(request, purchase_order_id):
    purchase_order = get_object_or_404(
        PurchaseOrder, 
        purchase_order_id=purchase_order_id, 
        is_deleted=False
    )
    
    # Check if the PO is still editable (only DRAFT)
    is_editable = purchase_order.status == PurchaseOrder.STATUS_DRAFT
    
    # Can send request if DRAFT and has items
    can_send_request = (
        purchase_order.status == PurchaseOrder.STATUS_DRAFT 
        and purchase_order.items.exists()
    )

    item_formset = None # Initialize item_formset

    if is_editable:
        if request.method == 'POST':
            # Create the formset for validation, regardless of the button pressed
            item_formset = PurchaseOrderItemFormSet(request.POST, instance=purchase_order)

            if item_formset.is_valid():
                # -----------------------------------------------
                # 1. ACTION: SEND REQUEST (Triggered by 'send_request' button)
                # -----------------------------------------------
                if 'send_request' in request.POST:
                    
                    with transaction.atomic():
                        # Save any items/changes before status transition
                        item_formset.save()
                        purchase_order.calculate_total_cost() 

                        # Final item check after saving (in case user deleted all items)
                        if not purchase_order.items.exists():
                            messages.error(request, "Cannot send request: No items in the order.")
                            return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
                        
                        # Status Transition
                        prev_status = purchase_order.status
                        purchase_order.status = PurchaseOrder.STATUS_REQUEST_PENDING
                        purchase_order.save(update_fields=['status'])
                        
                        # Audit: PO send request
                        try:
                            log_audit(
                                user=request.user,
                                action='update',
                                instance=purchase_order,
                                changes={'status': [prev_status, PurchaseOrder.STATUS_REQUEST_PENDING]},
                                request=request,
                                extra={'purchase_order_id': purchase_order.purchase_order_id, 'supplier_id': getattr(purchase_order, 'supplier_profile_id', None)}
                            )
                        except Exception:
                            pass
                        
                        # Send email to supplier
                        try:
                            # send_purchase_order_email(purchase_order) # Uncomment when imported
                            messages.success(
                                request, 
                                f"Purchase Order {purchase_order.purchase_order_id} sent to {purchase_order.supplier_profile.company_name}. "
                                "Awaiting supplier pricing."
                            )
                        except Exception as e:
                            messages.warning(
                                request, 
                                f"PO status updated, but email failed to send: {str(e)}"
                            )
                    
                    return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
                
                # -----------------------------------------------
                # 2. ACTION: SAVE ITEMS (Triggered by 'save_items' button or default submit)
                # -----------------------------------------------
                else: 
                    with transaction.atomic():
                        item_formset.save()
                        purchase_order.calculate_total_cost()
                        messages.success(request, "Purchase Order items updated successfully.")
                    
                    return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
            
            # 3. ACTION: VALIDATION FAILED (item_formset is passed to context with errors)
            else:
                messages.error(request, "Error saving items. Please check the form errors below.")

        else:
            # GET request: Initialize formset with instance data
            item_formset = PurchaseOrderItemFormSet(instance=purchase_order)
    else:
        # Not editable: display existing items only
        item_formset = None
        
    # --- Action URL Logic for Next Major Step ---
    next_action_url = None
    next_action_label = None

    if purchase_order.status == PurchaseOrder.STATUS_DRAFT:
        # Draft can be sent to supplier if it has items
        if can_send_request:
            next_action_url = None  # Handled by can_send_request button in template
            next_action_label = "Send Request to Supplier"
    elif purchase_order.status == PurchaseOrder.STATUS_REQUEST_PENDING:
        # Waiting for supplier to price - no action needed from staff
        next_action_url = None
        next_action_label = None
    elif purchase_order.status == PurchaseOrder.STATUS_SUPPLIER_PRICED:
        # Supplier has priced - staff needs to review and confirm
        next_action_url = reverse('PO:po_confirm', args=[purchase_order_id])
        next_action_label = "Review & Confirm Order"
    # elif purchase_order.status == PurchaseOrder.STATUS_CONFIRMED:
    #     # Confirmed - can mark as in transit or receive
    #     next_action_url = reverse('PO:po_mark_in_transit', args=[purchase_order_id])
    #     next_action_label = "Mark as In Transit"
    elif purchase_order.status == PurchaseOrder.STATUS_IN_TRANSIT:
        # In transit - can receive shipment
        next_action_url = reverse('PO:po_receive', args=[purchase_order_id])
        next_action_label = "Receive Shipment"
    elif purchase_order.status == PurchaseOrder.STATUS_PARTIALLY_RECEIVED:
        # Partially received - can continue receiving or request refund
        next_action_url = reverse('PO:po_receive', args=[purchase_order_id])
        next_action_label = "Continue Receiving"
    elif purchase_order.status == PurchaseOrder.STATUS_RECEIVED:
        # Fully received - can request refund if needed
        next_action_url = reverse('PO:po_request_refund', args=[purchase_order_id])
        next_action_label = "Request Refund"
    elif purchase_order.status == PurchaseOrder.STATUS_REFUND:
        # Already refunded - no action
        next_action_url = None
        next_action_label = None
    elif purchase_order.status == PurchaseOrder.STATUS_CANCELLED:
        # Cancelled - no action
        next_action_url = None
        next_action_label = None
    context = {
        'purchase_order': purchase_order,
        'item_formset': item_formset,
        'is_editable': is_editable,
        'can_send_request': can_send_request,
        'next_action_url': next_action_url,
        'next_action_label': next_action_label,
    }
    return render(request, 'purchase_orders/purchase_order_detail.html', context)


# --------------------------------------
# Staff/Manager PO Confirmation View
# --------------------------------------
@login_required
def po_confirm_view(request, purchase_order_id):
    """
    Staff/Manager reviews a 'supplier_priced' PO, finalizes payment terms, 
    and changes status to 'confirmed'.
    """
    purchase_order = get_object_or_404(
        PurchaseOrder, 
        purchase_order_id=purchase_order_id, 
        is_deleted=False
    )

    # Access Control
    if purchase_order.status != PurchaseOrder.STATUS_SUPPLIER_PRICED:
        messages.error(
            request, 
            f"PO cannot be confirmed. Current status is {purchase_order.get_status_display()}."
        )
        return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)

    purchase_order.calculate_total_cost()
    
    if request.method == "POST":
        form = POConfirmationForm(request.POST, request.FILES, instance=purchase_order)
        if form.is_valid():
            with transaction.atomic():
                confirmed_po = form.save(commit=False)
                
                # ✅ Ensure payment_due_date is set for net_30 payment method
                if confirmed_po.payment_method == "net_30" and not confirmed_po.payment_due_date:
                    from datetime import timedelta
                    confirmed_po.payment_due_date = timezone.now().date() + timedelta(days=30)
                    confirmed_po.pay_later = True  # Set the pay_later flag
                
                # Handle payment proof for prepaid
                if confirmed_po.payment_method == 'prepaid':
                    if 'payment_proof_image' in request.FILES:
                        confirmed_po.payment_proof_image = request.FILES['payment_proof_image']
                        # Mark as paid when proof is uploaded
                        confirmed_po.payment_status = 'paid'
                        confirmed_po.payment_verified_at = timezone.now()
                        confirmed_po.payment_verified_by = request.user
                
                prev_status = confirmed_po.status
                confirmed_po.status = PurchaseOrder.STATUS_CONFIRMED
                confirmed_po.save()

                # Audit: PO confirmed
                try:
                    log_audit(
                        user=request.user,
                        action='approve',
                        instance=confirmed_po,
                        changes={'status': [prev_status, PurchaseOrder.STATUS_CONFIRMED]},
                        request=request,
                        extra={'purchase_order_id': confirmed_po.purchase_order_id}
                    )
                except Exception:
                    pass
                
                messages.success(
                    request, 
                    f"Purchase Order {confirmed_po.purchase_order_id} CONFIRMED! "
                    
                )
            
            return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
        else:
            messages.error(request, "Error confirming PO. Please check the form fields.")
    else:
        form = POConfirmationForm(instance=purchase_order)
    
    context = {
        'purchase_order': purchase_order,
        'items': purchase_order.items.all(),
        'form': form,
        'action_url': reverse('PO:po_confirm', args=[purchase_order_id]),
    }
    return render(request, 'purchase_orders/po_confirm.html', context)


# --------------------------------------
# Staff/Manager PO Receiving View
# --------------------------------------
def handle_inventory_and_expense(purchase_order):
    """
    Update inventory when a PO is received.
    """
    for item in purchase_order.items.select_related('product', 'product_variant__product'):
        qty = int(item.quantity_received or item.quantity_ordered or 0)
        if qty <= 0:
            continue

        product_obj = None
        if item.product_variant:
            product_obj = getattr(item.product_variant, 'product', None)
        elif item.product:
            product_obj = item.product
        else:
            name = (item.product_name_text or "").strip()
            if name:
                product_obj = Product.objects.filter(name__iexact=name).first()
            if not product_obj:
                # Remove the local import - Product is already imported at top of file
                product_obj = Product.objects.create(
                    name = name or f"PO Item {item.id}",
                    price = item.unit_cost or Decimal('0.00'),
                    cost_price = item.unit_cost or Decimal('0.00'),
                    last_purchase_price = item.unit_cost or Decimal('0.00'),
                    stock_quantity = 0,
                    supplier_profile = purchase_order.supplier_profile
                )

        if not product_obj:
            continue

        product_obj.stock_quantity = (product_obj.stock_quantity or 0) + qty
        if item.unit_cost is not None:
            product_obj.last_purchase_price = item.unit_cost
            product_obj.cost_price = item.unit_cost
        product_obj.save(update_fields=['stock_quantity', 'last_purchase_price', 'cost_price'])

        try:
            from apps.inventory.models import StockMovement
            StockMovement.objects.create(
                product=product_obj, 
                movement_type='IN', 
                quantity=qty
            )
        except Exception:
            pass

        item.quantity_received = (item.quantity_received or 0) + qty
        item.save(update_fields=['quantity_received'])

    return True
@login_required
def po_mark_in_transit(request, purchase_order_id):
    """
    Staff/Manager marks a confirmed PO as 'In Transit' when shipment begins.
    """
    purchase_order = get_object_or_404(
        PurchaseOrder, 
        purchase_order_id=purchase_order_id, 
        is_deleted=False
    )

    # Access Control
    if purchase_order.status != PurchaseOrder.STATUS_CONFIRMED:
        messages.error(
            request, 
            f"PO cannot be marked as In Transit. Current status is {purchase_order.get_status_display()}."
        )
        return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)

    if request.method == "POST":
        with transaction.atomic():
            prev_status = purchase_order.status
            purchase_order.status = PurchaseOrder.STATUS_IN_TRANSIT
            purchase_order.save(update_fields=['status'])

            # Audit: PO marked in transit
            try:
                log_audit(
                    user=request.user,
                    action='update',
                    instance=purchase_order,
                    changes={'status': [prev_status, PurchaseOrder.STATUS_IN_TRANSIT]},
                    request=request,
                    extra={'purchase_order_id': purchase_order.purchase_order_id}
                )
            except Exception:
                pass
            
            messages.success(
                request, 
                f"Purchase Order {purchase_order.purchase_order_id} marked as IN TRANSIT."
            )
            return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
    
    context = {
        'purchase_order': purchase_order,
    }
    return render(request, 'purchase_orders/po_mark_in_transit.html', context)


# ═════════════════════════════════════════════════════════════
# NEW VIEW: Request Refund/Return
# ═════════════════════════════════════════════════════════════
# Add this helper function after handle_inventory_and_expense (around line 440)
def reverse_inventory_for_refund(purchase_order):
    """
    Reverse inventory when a PO is refunded.
    Decreases stock by the quantity_received for each item.
    """
    for item in purchase_order.items.select_related('product', 'product_variant__product'):
        # Use quantity_received (what was actually received), not quantity_ordered
        qty = int(item.quantity_received or 0)
        if qty <= 0:
            continue  # Skip if nothing was received for this item

        product_obj = None
        if item.product_variant:
            product_obj = getattr(item.product_variant, 'product', None)
        elif item.product:
            product_obj = item.product
        else:
            # Try to find by product_name_text
            name = (item.product_name_text or "").strip()
            if name:
                product_obj = Product.objects.filter(name__iexact=name).first()

        if not product_obj:
            continue  # Skip if product not found

        # Decrease stock (reverse the addition)
        current_stock = product_obj.stock_quantity or 0
        new_stock = max(0, current_stock - qty)  # Don't go below 0
        product_obj.stock_quantity = new_stock
        product_obj.save(update_fields=['stock_quantity'])

        # Create reverse stock movement entry
        try:
            from apps.inventory.models import StockMovement
            StockMovement.objects.create(
                product=product_obj, 
                movement_type='OUT',  # OUT for refund/reversal
                quantity=qty,
                notes=f"Refund reversal for PO {purchase_order.purchase_order_id}"
            )
        except Exception:
            pass

    return True


# Now update the po_request_refund function (replace lines 527-555)
@login_required
def po_request_refund(request, purchase_order_id):
    """
    Staff/Manager requests a refund for a received PO (damaged goods, wrong items, etc.)
    """
    purchase_order = get_object_or_404(
        PurchaseOrder, 
        purchase_order_id=purchase_order_id, 
        is_deleted=False
    )

    # Access Control - can only refund after received
    if purchase_order.status not in [PurchaseOrder.STATUS_RECEIVED, PurchaseOrder.STATUS_PARTIALLY_RECEIVED]:
        messages.error(
            request, 
            f"Refunds can only be requested for received orders. Current status is {purchase_order.get_status_display()}."
        )
        return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)

    if request.method == "POST":
        refund_reason = request.POST.get('refund_reason', '').strip()
        refund_amount_str = request.POST.get('refund_amount', '0')
        
        if not refund_reason:
            messages.error(request, "Refund reason is required.")
            return redirect('PO:po_request_refund', purchase_order_id=purchase_order_id)
        
        try:
            refund_amount = Decimal(refund_amount_str)
        except:
            refund_amount = purchase_order.total_cost
        
        with transaction.atomic():
            # Snapshot product stock before reversal
            product_stock_before = {}
            for item in purchase_order.items.select_related('product','product_variant__product').all():
                prod = (item.product_variant.product if item.product_variant and getattr(item.product_variant, 'product', None) else item.product)
                if prod:
                    product_stock_before[prod.id] = prod.stock_quantity or 0

            # Reverse inventory (decrease stock)
            success = reverse_inventory_for_refund(purchase_order)
            
            if not success:
                messages.error(request, "Error reversing inventory. Refund not processed.")
                return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
            
            prev_status = purchase_order.status
            purchase_order.status = PurchaseOrder.STATUS_REFUND
            purchase_order.refund_reason = refund_reason
            purchase_order.refund_amount = refund_amount
            purchase_order.save(update_fields=['status', 'refund_reason', 'refund_amount'])

            # Compute per-product stock changes for audit
            product_changes = {}
            for item in purchase_order.items.select_related('product','product_variant__product').all():
                prod = (item.product_variant.product if item.product_variant and getattr(item.product_variant, 'product', None) else item.product)
                if not prod:
                    continue
                before = product_stock_before.get(prod.id, 0)
                after = prod.stock_quantity or 0
                if before != after:
                    product_changes[str(prod.id)] = {
                        'product': str(prod), 
                        'before': before, 
                        'after': after, 
                        'delta': after - before  # Should be negative
                    }

            # Audit: PO refund requested with stock reversal
            try:
                changes = {
                    'status': [prev_status, PurchaseOrder.STATUS_REFUND],
                    'refund_reason': [None, refund_reason],
                    'refund_amount': [None, float(refund_amount)]
                }
                if product_changes:
                    changes['stock_reversal'] = product_changes
                
                log_audit(
                    user=request.user,
                    action='update',
                    instance=purchase_order,
                    changes=changes,
                    request=request,
                    extra={'purchase_order_id': purchase_order.purchase_order_id}
                )
            except Exception:
                pass
            
            messages.warning(
                request, 
                f"Refund request submitted for PO {purchase_order.purchase_order_id}. "
                f"Amount: ₱{refund_amount}. Stock has been reversed."
            )
            return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
    
    context = {
        'purchase_order': purchase_order,
    }
    return render(request, 'purchase_orders/po_request_refund.html', context)

@login_required
def po_receive_view(request, purchase_order_id):
    """
    Staff/Manager receives a PO (full or partial), updates inventory.
    Now properly handles IN_TRANSIT and PARTIALLY_RECEIVED statuses.
    """
    purchase_order = get_object_or_404(
        PurchaseOrder, 
        purchase_order_id=purchase_order_id, 
        is_deleted=False
    )

    # Access Control - can receive from CONFIRMED, IN_TRANSIT, or PARTIALLY_RECEIVED
    if purchase_order.status not in [
        PurchaseOrder.STATUS_CONFIRMED, 
        PurchaseOrder.STATUS_IN_TRANSIT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED
    ]:
        messages.error(
            request, 
            f"PO cannot be received. Current status is {purchase_order.get_status_display()}."
        )
        return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)

    if request.method == "POST":
        with transaction.atomic():
            # Snapshot product stock before
            product_stock_before = {}
            for item in purchase_order.items.select_related('product','product_variant__product').all():
                prod = (item.product_variant.product if item.product_variant and getattr(item.product_variant, 'product', None) else item.product)
                if prod:
                    product_stock_before[prod.id] = prod.stock_quantity or 0

            success = handle_inventory_and_expense(purchase_order)
            
            if success:
                prev_status = purchase_order.status
                
                # Check if fully received
                total_ordered = sum(item.quantity_ordered for item in purchase_order.items.all())
                total_received = sum(item.quantity_received for item in purchase_order.items.all())
                
                if total_received >= total_ordered:
                    purchase_order.status = PurchaseOrder.STATUS_RECEIVED
                    purchase_order.received_date = timezone.now().date()
                    messages.success(
                        request, 
                        f"Purchase Order {purchase_order.purchase_order_id} FULLY RECEIVED. "
                        "Inventory updated."
                    )
                else:
                    purchase_order.status = PurchaseOrder.STATUS_PARTIALLY_RECEIVED
                    messages.info(
                        request, 
                        f"Purchase Order {purchase_order.purchase_order_id} PARTIALLY RECEIVED. "
                        f"Received {total_received}/{total_ordered} items."
                    )
                
                purchase_order.save()

                # Compute per-product deltas
                product_changes = {}
                for item in purchase_order.items.select_related('product','product_variant__product').all():
                    prod = (item.product_variant.product if item.product_variant and getattr(item.product_variant, 'product', None) else item.product)
                    if not prod:
                        continue
                    before = product_stock_before.get(prod.id, 0)
                    after = prod.stock_quantity or 0
                    if before != after:
                        product_changes[str(prod.id)] = {
                            'product': str(prod), 
                            'before': before, 
                            'after': after, 
                            'delta': after - before
                        }

                # Audit
                def _log_receive():
                    try:
                        changes = {'status': [prev_status, purchase_order.status]}
                        if product_changes:
                            changes['stock_updates'] = product_changes
                        log_audit(
                            user=request.user, 
                            action='update', 
                            instance=purchase_order, 
                            changes=changes, 
                            request=request
                        )
                    except Exception:
                        pass
                transaction.on_commit(_log_receive)
                
                return redirect('PO:purchase_order_detail', purchase_order_id=purchase_order_id)
            else:
                messages.error(request, "Error receiving PO. Inventory update failed.")
                 
    context = {
        'purchase_order': purchase_order,
    }
    return render(request, 'purchase_orders/po_receive.html', context)



# --------------------------------------
# Archived & Delete/Restore POs
# --------------------------------------
@login_required
def archived_purchase_orders(request):
    archived_orders = PurchaseOrder.objects.filter(is_deleted=True)
    context = {
        'archived_orders': archived_orders,
        'page_title': 'Archived Purchase Orders',
    }
    return render(request, 'purchase_orders/archived_purchase_orders.html', context)


@csrf_exempt
def delete_purchase_orders(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            po_ids_to_delete = data.get('ids', [])
            orders = PurchaseOrder.objects.filter(purchase_order_id__in=po_ids_to_delete)
            for order in orders:
                order.delete()
            return JsonResponse({'success': True, 'message': f"{orders.count()} POs soft-deleted."})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@csrf_exempt
def restore_purchase_orders(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ids = data.get('ids', [])
            restored_count = PurchaseOrder.objects.filter(
                purchase_order_id__in=ids
            ).update(is_deleted=False, deleted_at=None)
            return JsonResponse({'success': True, 'message': f"{restored_count} POs restored."})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@csrf_exempt
def permanently_delete_purchase_orders(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ids = data.get('ids', [])
            deleted_count = PurchaseOrder.objects.filter(purchase_order_id__in=ids).delete()
            return JsonResponse({'success': True, 'message': f"{deleted_count[0]} POs permanently deleted."})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def approve_purchase_order(request, po_id):
    po = get_object_or_404(PurchaseOrder, pk=po_id)
    prev_status = getattr(po, 'status', None)
    po.status = 'approved'
    po.approved_by = request.user
    po.approved_at = timezone.now()
    po.save(update_fields=['status', 'approved_by', 'approved_at'])

    # explicit audit log entry (small summary)
    try:
        log_audit(
            user=request.user,
            action='approve',
            instance=po,
            changes={'status': [prev_status, po.status]},
            request=request,
            extra={'supplier_id': getattr(po, 'supplier_profile_id', None)}
        )
    except Exception:
        pass

    messages.success(request, "Purchase order approved.")
    return redirect('purchase_orders:detail', po.id)


# Add these imports to your existing imports in apps/purchasing/views.py
from django.template.loader import get_template
from django.http import HttpResponse
from xhtml2pdf import pisa
import io

# Add these two new views to your apps/purchasing/views.py

@login_required
def purchase_order_print_view(request, purchase_order_id):
    """
    Display printable purchase order (view-only, auto-filled)
    """
    purchase_order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier_profile', 'created_by')
        .prefetch_related('items__product_variant__product', 'items__product'),
        purchase_order_id=purchase_order_id,
        is_deleted=False
    )
    
    # Get order items
    items = purchase_order.items.select_related(
        'product_variant__product', 
        'product'
    ).all()
    
    # Calculate totals
    subtotal = purchase_order.total_cost
    tax = 0  # Add tax calculation if needed
    total = subtotal + tax
    
    # Get supplier information
    supplier_name = purchase_order.supplier_profile.company_name if purchase_order.supplier_profile else 'N/A'
    supplier_email = purchase_order.supplier_profile.user.email if purchase_order.supplier_profile else 'N/A'
    supplier_phone = purchase_order.supplier_profile.phone if purchase_order.supplier_profile else 'N/A'
    supplier_address = purchase_order.supplier_profile.address if purchase_order.supplier_profile else 'N/A'
    
    context = {
        'purchase_order': purchase_order,
        'po_number': purchase_order.purchase_order_id,
        'order_date': purchase_order.order_date,
        'expected_delivery_date': purchase_order.expected_delivery_date,
        'supplier_name': supplier_name,
        'supplier_email': supplier_email,
        'supplier_phone': supplier_phone,
        'supplier_address': supplier_address,
        'items': items,
        'subtotal': subtotal,
        'tax': tax,
        'total': total,
        'payment_method': purchase_order.get_payment_method_display(),
        'status': purchase_order.get_status_display(),
        'notes': purchase_order.notes or 'No additional notes',
        'created_by': purchase_order.created_by.get_full_name() if purchase_order.created_by else 'N/A',
        'page_title': f'Purchase Order - {purchase_order.purchase_order_id}'
    }
    
    return render(request, 'purchase_orders/purchase_order_print.html', context)


@login_required
def purchase_order_pdf(request, purchase_order_id):
    """
    Generate downloadable PDF for purchase order
    """
    purchase_order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier_profile', 'created_by')
        .prefetch_related('items__product_variant__product', 'items__product'),
        purchase_order_id=purchase_order_id,
        is_deleted=False
    )
    
    # Get order items
    items = purchase_order.items.select_related(
        'product_variant__product', 
        'product'
    ).all()
    
    # Calculate totals
    subtotal = purchase_order.total_cost
    tax = 0  # Add tax calculation if needed
    total = subtotal + tax
    
    # Get supplier information
    supplier_name = purchase_order.supplier_profile.company_name if purchase_order.supplier_profile else 'N/A'
    supplier_email = purchase_order.supplier_profile.user.email if purchase_order.supplier_profile else 'N/A'
    supplier_phone = purchase_order.supplier_profile.phone if purchase_order.supplier_profile else 'N/A'
    supplier_address = purchase_order.supplier_profile.address if purchase_order.supplier_profile else 'N/A'
    
    context = {
        'purchase_order': purchase_order,
        'po_number': purchase_order.purchase_order_id,
        'order_date': purchase_order.order_date,
        'expected_delivery_date': purchase_order.expected_delivery_date,
        'supplier_name': supplier_name,
        'supplier_email': supplier_email,
        'supplier_phone': supplier_phone,
        'supplier_address': supplier_address,
        'items': items,
        'subtotal': subtotal,
        'tax': tax,
        'total': total,
        'payment_method': purchase_order.get_payment_method_display(),
        'status': purchase_order.get_status_display(),
        'notes': purchase_order.notes or 'No additional notes',
        'created_by': purchase_order.created_by.get_full_name() if purchase_order.created_by else 'N/A',
        'is_pdf': True,
    }

    # --- Generate PDF ---
    template = get_template('purchase_orders/purchase_order_pdf.html')
    html = template.render(context)
    source_html = html.encode("UTF-8")
    result = io.BytesIO()
    pdf = pisa.pisaDocument(io.BytesIO(source_html), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"PurchaseOrder_{purchase_order.purchase_order_id}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        return HttpResponse("Error generating PDF", status=500)

# --------------------------------------
# PO Billing Dashboard & Detail (separate from customer orders)
# --------------------------------------
from django.utils import timezone
from django.db.models import Q

@login_required
def po_billing_dashboard(request):
    """
    Billing dashboard for Purchase Orders (supplier-side).
    Includes payment status tracking with overdue detection.
    """
    today = timezone.now().date()

    pos = PurchaseOrder.objects.filter(is_deleted=False).select_related('supplier_profile').order_by('-order_date')

    # Helper function
    def get_total_cost_sum(queryset):
        return sum(po.total_cost for po in queryset)

    # KPIs based on payment status
    paid_pos = pos.filter(payment_status='paid')
    paid_total_revenue = get_total_cost_sum(paid_pos)
    
    unpaid_pos = pos.filter(payment_status='unpaid')
    unpaid_total_amount = get_total_cost_sum(unpaid_pos)
    
    overdue_pos = pos.filter(payment_status='overdue')
    overdue_total_amount = get_total_cost_sum(overdue_pos)
    
    refunded_pos = pos.filter(payment_status__in=['refunded', 'partially_refunded'])
    refunded_total_amount = get_total_cost_sum(refunded_pos)
    
    # Count of overdue POs
    overdue_count = overdue_pos.count()
    
    # Count of due soon pay-later (within 7 days)
    due_soon_count = pos.filter(
        pay_later=True, 
        payment_due_date__isnull=False, 
        payment_due_date__gte=today, 
        payment_due_date__lte=today + timezone.timedelta(days=7),
        payment_status='unpaid'
    ).count()
    
    # Received today
    paid_today_count = pos.filter(
        status=PurchaseOrder.STATUS_RECEIVED, 
        received_date=today
    ).count()

    context = {
        'paid_total_revenue': paid_total_revenue,
        'unpaid_total_amount': unpaid_total_amount,
        'overdue_total_amount': overdue_total_amount,
        'overdue_count': overdue_count,
        'refunded_total_amount': refunded_total_amount,
        'due_soon_count': due_soon_count,
        'paid_today_count': paid_today_count,
        'all_orders': list(pos),
    }

    return render(request, 'purchase_orders/po_billing_dashboard.html', context)
@login_required
def po_billing_order_detail(request, po_id):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier_profile', 'created_by')
        .prefetch_related('items__product_variant__product', 'items__product'),
        id=po_id,
        is_deleted=False,
    )

    items = [{
        'product_name': (item.product_variant.product.name if item.product_variant else (item.product.name if item.product else (item.product_name_text or 'Item'))),
        'variant': (getattr(item.product_variant, 'name', getattr(item.product_variant, 'sku', 'Default')) if item.product_variant else 'Default'),
        'quantity': item.quantity_ordered,
        'price': float(item.unit_cost or 0),
        'total': float(item.total_price),
    } for item in po.items.all()]

    supplier_name = po.supplier_profile.company_name if po.supplier_profile else 'N/A'
    supplier_email = po.supplier_profile.user.email if po.supplier_profile else 'N/A'
    supplier_phone = po.supplier_profile.phone if po.supplier_profile else 'N/A'
    supplier_address = po.supplier_profile.address if po.supplier_profile else 'N/A'

    context = {
        'page_type': 'po',
        'purchase_order': po,
        'order_id': po.purchase_order_id,
        'customer_name': supplier_name,
        'customer_email': supplier_email,
        'shipping_phone': supplier_phone,
        'shipping_address': supplier_address,
        'status': po.get_status_display(),
        'payment_method': po.get_payment_method_display(),
        'payment_status': po.payment_status or 'unpaid',
        'payment_verified_at': po.payment_verified_at,
        'payment_due_date': po.payment_due_date,
        'order_date': po.order_date,
        'total_cost': float(po.total_cost),
        'payment_proof_image': po.payment_proof_image,
        'items': items,
    }

    return render(request, 'purchase_orders/po_billing_order_detail.html', context)


@login_required
def po_upload_payment_proof(request, po_id):
    """Upload payment proof for pay later/net_30 orders"""
    po = get_object_or_404(
        PurchaseOrder,
        id=po_id,
        is_deleted=False,
    )
    
    # Only allow upload for net_30/pay_later orders that are unpaid
    if po.payment_method not in ['net_30'] and not po.pay_later:
        messages.error(request, "Payment proof upload is only available for pay later orders.")
        return redirect('PO:po_billing_order_detail', po_id=po_id)
    
    if request.method == 'POST':
        from .forms import PaymentProofUploadForm
        form = PaymentProofUploadForm(request.POST, request.FILES, instance=po)
        if form.is_valid():
            with transaction.atomic():
                po = form.save(commit=False)
                # Mark as paid when proof is uploaded
                po.payment_status = 'paid'
                po.payment_verified_at = timezone.now()
                po.payment_verified_by = request.user
                po.save()
                
                # Audit log
                try:
                    log_audit(
                        user=request.user,
                        action='update',
                        instance=po,
                        changes={'payment_status': ['unpaid', 'paid'], 'payment_proof': [None, 'uploaded']},
                        request=request,
                        extra={'purchase_order_id': po.purchase_order_id}
                    )
                except Exception:
                    pass
                
                messages.success(request, f"Payment proof uploaded successfully. PO {po.purchase_order_id} marked as paid.")
                return redirect('PO:po_billing_order_detail', po_id=po_id)
        else:
            messages.error(request, "Error uploading payment proof. Please check the file.")
    else:
        from .forms import PaymentProofUploadForm
        form = PaymentProofUploadForm(instance=po)
    
    context = {
        'purchase_order': po,
        'form': form,
    }
    return render(request, 'purchase_orders/po_upload_payment_proof.html', context)

# Add these imports to your existing imports in apps/purchasing/views.py
from django.template.loader import get_template
from django.http import HttpResponse
from xhtml2pdf import pisa
import io

# Add these two new views to your apps/purchasing/views.py

@login_required
def purchase_order_print_view(request, purchase_order_id):
    """
    Display printable purchase order (view-only, auto-filled)
    """
    purchase_order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier_profile', 'created_by')
        .prefetch_related('items__product_variant__product', 'items__product'),
        purchase_order_id=purchase_order_id,
        is_deleted=False
    )
    
    # Get order items
    items = purchase_order.items.select_related(
        'product_variant__product', 
        'product'
    ).all()
    
    # Calculate totals
    subtotal = purchase_order.total_cost
    tax = 0  # Add tax calculation if needed
    total = subtotal + tax
    
    # Get supplier information
    supplier_name = purchase_order.supplier_profile.company_name if purchase_order.supplier_profile else 'N/A'
    supplier_email = purchase_order.supplier_profile.user.email if purchase_order.supplier_profile else 'N/A'
    supplier_phone = purchase_order.supplier_profile.phone if purchase_order.supplier_profile else 'N/A'
    supplier_address = purchase_order.supplier_profile.address if purchase_order.supplier_profile else 'N/A'
    
    context = {
        'purchase_order': purchase_order,
        'po_number': purchase_order.purchase_order_id,
        'order_date': purchase_order.order_date,
        'expected_delivery_date': purchase_order.expected_delivery_date,
        'supplier_name': supplier_name,
        'supplier_email': supplier_email,
        'supplier_phone': supplier_phone,
        'supplier_address': supplier_address,
        'items': items,
        'subtotal': subtotal,
        'tax': tax,
        'total': total,
        'payment_method': purchase_order.get_payment_method_display(),
        'status': purchase_order.get_status_display(),
        'notes': purchase_order.notes or 'No additional notes',
        'created_by': purchase_order.created_by.get_full_name() if purchase_order.created_by else 'N/A',
        'page_title': f'Purchase Order - {purchase_order.purchase_order_id}'
    }
    
    return render(request, 'purchase_orders/purchase_order_print.html', context)


@login_required
def purchase_order_pdf(request, purchase_order_id):
    """
    Generate downloadable PDF for purchase order
    """
    purchase_order = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier_profile', 'created_by')
        .prefetch_related('items__product_variant__product', 'items__product'),
        purchase_order_id=purchase_order_id,
        is_deleted=False
    )
    
    # Get order items
    items = purchase_order.items.select_related(
        'product_variant__product', 
        'product'
    ).all()
    
    # Calculate totals
    subtotal = purchase_order.total_cost
    tax = 0  # Add tax calculation if needed
    total = subtotal + tax
    
    # Get supplier information
    supplier_name = purchase_order.supplier_profile.company_name if purchase_order.supplier_profile else 'N/A'
    supplier_email = purchase_order.supplier_profile.user.email if purchase_order.supplier_profile else 'N/A'
    supplier_phone = purchase_order.supplier_profile.phone if purchase_order.supplier_profile else 'N/A'
    supplier_address = purchase_order.supplier_profile.address if purchase_order.supplier_profile else 'N/A'
    
    context = {
        'purchase_order': purchase_order,
        'po_number': purchase_order.purchase_order_id,
        'order_date': purchase_order.order_date,
        'expected_delivery_date': purchase_order.expected_delivery_date,
        'supplier_name': supplier_name,
        'supplier_email': supplier_email,
        'supplier_phone': supplier_phone,
        'supplier_address': supplier_address,
        'items': items,
        'subtotal': subtotal,
        'tax': tax,
        'total': total,
        'payment_method': purchase_order.get_payment_method_display(),
        'status': purchase_order.get_status_display(),
        'notes': purchase_order.notes or 'No additional notes',
        'created_by': purchase_order.created_by.get_full_name() if purchase_order.created_by else 'N/A',
        'is_pdf': True,
    }

    # --- Generate PDF ---
    template = get_template('purchase_orders/purchase_order_pdf.html')
    html = template.render(context)
    source_html = html.encode("UTF-8")
    result = io.BytesIO()
    pdf = pisa.pisaDocument(io.BytesIO(source_html), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"PurchaseOrder_{purchase_order.purchase_order_id}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        return HttpResponse("Error generating PDF", status=500)