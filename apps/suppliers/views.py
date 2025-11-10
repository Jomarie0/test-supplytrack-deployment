from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.conf import settings
from django.core.mail import send_mail
from decimal import Decimal

from apps.purchase_orders.models import PurchaseOrder
from apps.users.models import SupplierProfile


# -----------------------
# EMAIL HELPERS (Your code is good)
# -----------------------
def send_order_priced_email_to_admin(purchase_order):
    """Email to notify admin that supplier has submitted pricing."""
    admin_email = getattr(settings, "ADMIN_EMAIL", None)
    if not admin_email:
        print("Admin email not set in settings")
        return

    items_details = ""
    for item in purchase_order.items.all():
        product_name = (
            item.product_variant.product.name
            if item.product_variant
            else item.product_name_text
        )
        items_details += (
            f"- {item.quantity_ordered}x {product_name} @ ₱{item.unit_cost:.2f} each\n"
        )

    subject = (
        f"ACTION REQUIRED: Pricing Submitted for PO {purchase_order.purchase_order_id}"
    )
    message = (
        f"Supplier '{purchase_order.supplier_profile.company_name}' has submitted pricing for PO {purchase_order.purchase_order_id}.\n\n"
        f"Total Cost: ₱{purchase_order.total_cost:.2f}\n\n"
        f"ITEMS PRICED:\n{items_details}\n"
        "Please log in to your dashboard to review and confirm the order."
    )
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[admin_email],
        fail_silently=False,
    )


def send_order_cancelled_email_to_admin(purchase_order):
    """Email to notify admin that supplier has cancelled an order."""
    # This function is fine as is.
    pass  # Assuming your original function is here


# -----------------------
# SUPPLIER DASHBOARD VIEWS
# -----------------------
@login_required
def supplier_dashboard(request):
    try:
        supplier = SupplierProfile.objects.get(user=request.user)
    except SupplierProfile.DoesNotExist:
        messages.error(request, "No supplier profile linked to your account.")
        return redirect("users:supplier_login")

    all_orders = PurchaseOrder.objects.filter(
        supplier_profile=supplier, is_deleted=False
    )

    pending_pricing_count = all_orders.filter(
        status=PurchaseOrder.STATUS_REQUEST_PENDING
    ).count()
    awaiting_confirmation_count = all_orders.filter(
        status=PurchaseOrder.STATUS_SUPPLIER_PRICED
    ).count()
    in_transit_count = all_orders.filter(
        status=PurchaseOrder.STATUS_IN_TRANSIT
    ).count()
    completed_count = all_orders.filter(
        status__in=[
            PurchaseOrder.STATUS_CONFIRMED,
            PurchaseOrder.STATUS_RECEIVED,
            PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
        ]
    ).count()

    recent_orders = (
        all_orders.order_by("-order_date")
        .select_related("supplier_profile")
        [:5]
    )

    context = {
        "supplier": supplier,
        "total_orders": all_orders.count(),
        "pending_pricing_count": pending_pricing_count,
        "awaiting_confirmation_count": awaiting_confirmation_count,
        "in_transit_count": in_transit_count,
        "completed_count": completed_count,
        "recent_orders": recent_orders,
    }
    return render(request, "suppliers/supplier_dashboard.html", context)

@login_required
def supplier_order_list(request):
    try:
        supplier = SupplierProfile.objects.get(user=request.user)
    except SupplierProfile.DoesNotExist:
        messages.error(request, "No supplier profile linked to your account.")
        return redirect("users:supplier_login")

    # This logic is good. It shows all relevant POs to the supplier.
    purchase_orders = (
        PurchaseOrder.objects.filter(supplier_profile=supplier, is_deleted=False)
        .exclude(status=PurchaseOrder.STATUS_DRAFT)
        .order_by("-order_date")
    )

    # ... Your filtering logic is fine ...

    context = {
        "supplier": supplier,
        "purchase_orders": purchase_orders,
    }
    return render(request, "suppliers/supplier_order_list.html", context)


@login_required
def supplier_view_order(request, purchase_order_id):
    """
    REFACTORED: A simple, read-only detail view for any non-pending PO.
    The primary action view is now `supplier_price_order`.
    """
    try:
        supplier = SupplierProfile.objects.get(user=request.user)
    except SupplierProfile.DoesNotExist:
        messages.error(request, "No supplier profile linked to your account.")
        return redirect("users:supplier_login")

    purchase_order = get_object_or_404(
        PurchaseOrder,
        purchase_order_id=purchase_order_id,
        supplier_profile=supplier,
        is_deleted=False,
    )

    # This view is now for viewing details, not for actions.
    # The action is handled by the `price_order` view.
    context = {
        "purchase_order": purchase_order,
        "items": purchase_order.items.all(),
        "supplier": supplier,
    }
    return render(request, "suppliers/view_order.html", context)


# -----------------------
# SUPPLIER PRICING VIEW (YOUR NEW LOGIC - LOOKS GREAT!)
# -----------------------
@login_required
def supplier_price_order(request, purchase_order_id):
    """
    Allows the supplier to submit unit prices for a PENDING PO.
    Changes status from 'request_pending' to 'supplier_priced'.
    """
    try:
        supplier_profile = SupplierProfile.objects.get(user=request.user)
    except SupplierProfile.DoesNotExist:
        messages.error(request, "No supplier profile linked to your account.")
        return redirect("users:supplier_login")

    purchase_order = get_object_or_404(
        PurchaseOrder,
        purchase_order_id=purchase_order_id,
        supplier_profile=supplier_profile,
        is_deleted=False,
    )

    # Access Control: Supplier can only PRICE a PO that is PENDING or already priced (for edits)
    if purchase_order.status not in [
        purchase_order.STATUS_REQUEST_PENDING,
        purchase_order.STATUS_SUPPLIER_PRICED,
    ]:
        messages.error(
            request,
            f"Cannot modify PO. Current status is {purchase_order.get_status_display()}.",
        )
        return redirect("suppliers:supplier_order_list")

    if request.method == "POST":
        try:
            with transaction.atomic():
                items = purchase_order.items.all()
                all_items_priced = True

                # Loop through submitted item prices
                for item in items:
                    unit_cost_str = request.POST.get(f"unit_cost_{item.id}")

                    if not unit_cost_str:
                        all_items_priced = False
                        continue  # Skip if no price was submitted for this item

                    new_cost = Decimal(unit_cost_str)
                    if new_cost <= 0:
                        raise ValueError(
                            f"Invalid unit cost for '{item.product_name_text}'. Must be positive."
                        )

                    item.unit_cost = new_cost
                    item.save()  # This triggers the PO's calculate_total_cost method via signals/save override

                # Ensure all items have a price before changing status
                if not all_items_priced:
                    messages.warning(
                        request, "Please submit a unit price for all items."
                    )
                    # Re-render the form with the warning
                    context = {
                        "purchase_order": purchase_order,
                        "items": items,
                        "supplier": supplier_profile,
                    }
                    return render(request, "suppliers/price_order.html", context)

                # Get and save expected delivery date from supplier
                expected_delivery_date_str = request.POST.get("expected_delivery_date")
                if expected_delivery_date_str:
                    from django.utils.dateparse import parse_date
                    expected_delivery_date = parse_date(expected_delivery_date_str)
                    if expected_delivery_date:
                        purchase_order.expected_delivery_date = expected_delivery_date
                    else:
                        messages.error(request, "Invalid expected delivery date format.")
                        context = {
                            "purchase_order": purchase_order,
                            "items": items,
                            "supplier": supplier_profile,
                        }
                        return render(request, "suppliers/price_order.html", context)
                else:
                    messages.error(request, "Expected delivery date is required.")
                    context = {
                        "purchase_order": purchase_order,
                        "items": items,
                        "supplier": supplier_profile,
                    }
                    return render(request, "suppliers/price_order.html", context)

                # Update PO Status and notify admin
                purchase_order.status = purchase_order.STATUS_SUPPLIER_PRICED
                purchase_order.save(update_fields=["status", "expected_delivery_date"])

                send_order_priced_email_to_admin(purchase_order)

                messages.success(
                    request,
                    f"Pricing submitted for PO {purchase_order.purchase_order_id}. Awaiting staff approval.",
                )
                return redirect("suppliers:supplier_order_list")

        except (ValueError, TypeError) as e:
            messages.error(request, f"Invalid data submitted. {str(e)}")
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {str(e)}")

    context = {
        "purchase_order": purchase_order,
        "items": purchase_order.items.all(),
        "supplier": supplier_profile,
    }
    return render(request, "suppliers/price_order.html", context)
# Add these imports at the top if not already present
from django.db import transaction
from apps.transactions.models import log_audit

# Add these new views after supplier_price_order (around line 252)

@login_required
def supplier_mark_in_transit(request, purchase_order_id):
    """
    Supplier marks a confirmed PO as 'In Transit' when shipment begins.
    """
    try:
        supplier_profile = SupplierProfile.objects.get(user=request.user)
    except SupplierProfile.DoesNotExist:
        messages.error(request, "No supplier profile linked to your account.")
        return redirect("users:supplier_login")

    purchase_order = get_object_or_404(
        PurchaseOrder,
        purchase_order_id=purchase_order_id,
        supplier_profile=supplier_profile,
        is_deleted=False,
    )

    # Access Control: Supplier can only mark as in transit if status is CONFIRMED
    if purchase_order.status != PurchaseOrder.STATUS_CONFIRMED:
        messages.error(
            request,
            f"PO cannot be marked as In Transit. Current status is {purchase_order.get_status_display()}.",
        )
        return redirect("suppliers:supplier_order_list")

    if request.method == "POST":
        with transaction.atomic():
            prev_status = purchase_order.status
            purchase_order.status = PurchaseOrder.STATUS_IN_TRANSIT
            purchase_order.save(update_fields=['status'])

            # Audit: PO marked in transit by supplier
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
                f"Purchase Order {purchase_order.purchase_order_id} marked as IN TRANSIT. Shipment is on the way!",
            )
            return redirect("suppliers:supplier_order_list")

    context = {
        "purchase_order": purchase_order,
        "supplier": supplier_profile,
    }
    return render(request, "suppliers/mark_in_transit.html", context)


@login_required
def supplier_cancel_order(request, purchase_order_id):
    """
    Supplier cancels a PO that is pending pricing or already priced.
    """
    try:
        supplier_profile = SupplierProfile.objects.get(user=request.user)
    except SupplierProfile.DoesNotExist:
        messages.error(request, "No supplier profile linked to your account.")
        return redirect("users:supplier_login")

    purchase_order = get_object_or_404(
        PurchaseOrder,
        purchase_order_id=purchase_order_id,
        supplier_profile=supplier_profile,
        is_deleted=False,
    )

    # Access Control: Supplier can only cancel if status is REQUEST_PENDING or SUPPLIER_PRICED
    if purchase_order.status not in [
        PurchaseOrder.STATUS_REQUEST_PENDING,
        PurchaseOrder.STATUS_SUPPLIER_PRICED,
    ]:
        messages.error(
            request,
            f"Cannot cancel PO. Current status is {purchase_order.get_status_display()}. "
            "Only pending or priced orders can be cancelled.",
        )
        return redirect("suppliers:supplier_order_list")

    if request.method == "POST":
        cancel_reason = request.POST.get('cancel_reason', '').strip()
        
        if not cancel_reason:
            messages.error(request, "Please provide a reason for cancellation.")
            return redirect("suppliers:supplier_cancel_order", purchase_order_id=purchase_order_id)

        with transaction.atomic():
            prev_status = purchase_order.status
            purchase_order.status = PurchaseOrder.STATUS_CANCELLED
            if hasattr(purchase_order, 'notes'):
                existing_notes = purchase_order.notes or ""
                purchase_order.notes = f"{existing_notes}\n[CANCELLED BY SUPPLIER] Reason: {cancel_reason}".strip()
            purchase_order.save(update_fields=['status', 'notes'])

            # Audit: PO cancelled by supplier
            try:
                log_audit(
                    user=request.user,
                    action='cancel',
                    instance=purchase_order,
                    changes={
                        'status': [prev_status, PurchaseOrder.STATUS_CANCELLED],
                        'cancel_reason': [None, cancel_reason]
                    },
                    request=request,
                    extra={'purchase_order_id': purchase_order.purchase_order_id}
                )
            except Exception:
                pass

            # Send email notification to admin (optional - you can uncomment if needed)
            # send_order_cancelled_email_to_admin(purchase_order)

            messages.warning(
                request,
                f"Purchase Order {purchase_order.purchase_order_id} has been CANCELLED.",
            )
            return redirect("suppliers:supplier_order_list")

    context = {
        "purchase_order": purchase_order,
        "supplier": supplier_profile,
    }
    return render(request, "suppliers/cancel_order.html", context)