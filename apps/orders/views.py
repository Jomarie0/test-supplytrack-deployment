# apps/orders/views.py

import json
import logging
import random
import string
import traceback
from decimal import Decimal
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Sum, Prefetch, Q
from django.utils import timezone
from django.views.decorators.http import require_POST

# Models
from apps.delivery.models import Delivery

from apps.orders.models import Order, OrderItem, ManualOrder
from apps.inventory.models import Product, StockMovement
from apps.store.models import Cart
from apps.users.models import User

# Forms
from .forms import OrderForm, CheckoutForm, ManualOrderForm
from apps.transactions.models import log_audit  # added

# For Invoice
from io import BytesIO
from django.http import FileResponse, Http404
from datetime import datetime
from django.core.mail import EmailMessage
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

logger = logging.getLogger(__name__)


# ------------------------------
# CART HELPERS
# ------------------------------
def get_or_create_cart(request):
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user)
        if request.session.session_key:
            try:
                anon_cart = Cart.objects.get(
                    session_key=request.session.session_key, user__isnull=True
                )
                if anon_cart != cart:
                    for item in anon_cart.items.all():
                        existing_item = cart.items.filter(
                            product_variant=item.product_variant
                        ).first()
                        if existing_item:
                            existing_item.quantity += item.quantity
                            existing_item.save()
                        else:
                            item.cart = cart
                            item.save()
                    anon_cart.delete()
                del request.session["session_key"]
            except Cart.DoesNotExist:
                pass
    else:
        if not request.session.session_key:
            request.session.save()
        cart, _ = Cart.objects.get_or_create(
            session_key=request.session.session_key, user__isnull=True
        )
    return cart


# ------------------------------
# ORDER LIST & MANAGEMENT
# ------------------------------
from django.db.models import F, ExpressionWrapper, DecimalField
from django.contrib.auth.decorators import user_passes_test

# ============================================
# ROLE CHECKER DECORATORS
# ============================================


def is_admin_or_manager(user):
    """Check if user is admin or manager"""
    return user.is_authenticated and user.role in ["admin", "manager"]


def is_staff_or_above(user):
    """Check if user is staff, manager, or admin"""
    return user.is_authenticated and user.role in ["staff", "manager", "admin"]


def is_delivery_personnel(user):
    """Check if user is delivery personnel"""
    return user.is_authenticated and user.role == "delivery"


@login_required
@user_passes_test(is_staff_or_above, login_url="/permission-denied/")
def customer_orders_management(request):
    """
    Role-based order management view
    - ADMIN/MANAGER: See all orders, can approve Pending → Processing
    - STAFF: See only Processing orders, can update to Shipped
    """

    # Base queryset
    orders = (
        Order.objects.filter(is_deleted=False)
        .select_related("customer__customer_profile")
        .prefetch_related("items__product_variant__product")
    )

    # ROLE-BASED FILTERING
    user_role = request.user.role

    if user_role in ["admin", "manager"]:
        # Admin/Manager see ALL orders
        orders = orders.order_by("-order_date")
        can_approve_orders = True
        can_update_to_shipped = True

    elif user_role == "staff":
        # Staff only see Processing and Shipped orders
        orders = orders.filter(status__in=["Processing", "Shipped"]).order_by(
            "-order_date"
        )
        can_approve_orders = False
        can_update_to_shipped = True
    else:
        # Fallback: no access
        messages.error(request, "You don't have permission to access this page.")
        return redirect("home")

    # Annotate total cost per order
    orders = orders.annotate(
        total_cost_db=Sum(
            ExpressionWrapper(
                F("items__price_at_order") * F("items__quantity"),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        )
    )
    
    # KPIs (adjust based on role)
    total_orders = orders.count()
    pending_orders = orders.filter(status="Pending").count()
    processing_orders = orders.filter(status="Processing").count()
    shipped_orders = orders.filter(status="Shipped").count()
    completed_orders = orders.filter(status="Completed").count()
    canceled_orders = orders.filter(status="Canceled").count()

    completion_rate = (
        round((completed_orders / total_orders) * 100, 1) if total_orders else 0
    )
    cancellation_rate = (
        round((canceled_orders / total_orders) * 100, 1) if total_orders else 0
    )

    # Total revenue
    total_revenue = (
        orders.filter(status="Completed").aggregate(total=Sum("total_cost_db"))["total"]
        or 0
    )

    # Period comparison (last 30 days)
    from django.utils import timezone

    now_time = timezone.now()
    thirty_days_ago = now_time - timedelta(days=30)
    sixty_days_ago = now_time - timedelta(days=60)

    current_orders = orders.filter(order_date__gte=thirty_days_ago)
    previous_orders = orders.filter(
        order_date__gte=sixty_days_ago, order_date__lt=thirty_days_ago
    )

    def calc_change(current, previous):
        if previous > 0:
            return round(((current - previous) / previous) * 100, 1)
        return 100 if current > 0 else 0

    total_change = calc_change(current_orders.count(), previous_orders.count())
    revenue_change = calc_change(
        current_orders.filter(status="Completed").aggregate(total=Sum("total_cost_db"))[
            "total"
        ]
        or 0,
        previous_orders.filter(status="Completed").aggregate(
            total=Sum("total_cost_db")
        )["total"]
        or 0,
    )

    context = {
        "orders": orders,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "processing_orders": processing_orders,
        "shipped_orders": shipped_orders,
        "completed_orders": completed_orders,
        "canceled_orders": canceled_orders,
        "total_revenue": total_revenue,
        "completion_rate": completion_rate,
        "cancellation_rate": cancellation_rate,
        "total_change": total_change,
        "revenue_change": revenue_change,
        # ROLE-BASED PERMISSIONS
        "user_role": user_role,
        "can_approve_orders": can_approve_orders,
        "can_update_to_shipped": can_update_to_shipped,
    }

    return render(request, "orders/customer_orders_management.html", context)


# ------------------------------
# ORDER CRUD
# ------------------------------
@login_required
def order_list(request):
    form = OrderForm()
    if request.method == "POST":
        order_id_from_form = request.POST.get("order_id")
        if order_id_from_form:
            existing_order = get_object_or_404(Order, order_id=order_id_from_form)
            form = OrderForm(request.POST, instance=existing_order)
        else:
            form = OrderForm(request.POST)

        if form.is_valid():
            is_new = not order_id_from_form
            order = form.save(commit=False)
            if not order.order_id:
                order.order_id = "ORD" + "".join(
                    random.choices(string.ascii_uppercase + string.digits, k=6)
                )
            order.is_deleted = False
            order.save()
            messages.success(request, f"Order {order.order_id} successfully saved!")

            try:
                log_audit(
                    user=request.user,
                    action="create" if is_new else "update",
                    instance=order,
                    changes={"saved": True},
                    request=request,
                )
            except Exception:
                pass

            return redirect("orders:order_list")
        else:
            messages.error(request, "Error saving order. Check the form.")
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request, f"{field.replace('_', ' ').title()}: {error}"
                    )

    orders_queryset = (
        Order.objects.filter(is_deleted=False)
        .select_related("customer__customer_profile")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=OrderItem.objects.select_related("product_variant__product"),
            )
        )
        .order_by("-order_date")
    )

    # Status counts
    status_counts = {
        status: orders_queryset.filter(status=status).count()
        for status, _ in Order.ORDER_STATUS_CHOICES
    }

    context = {
        "orders": orders_queryset,
        "order_statuses_choices": Order.ORDER_STATUS_CHOICES,
        "form": form,
        **status_counts,
        "products": Product.objects.filter(is_deleted=False),
        "customers": User.objects.all(),
    }
    return render(request, "orders/orders_list.html", context)


# ------------------------------
# ARCHIVE / DELETE / RESTORE
# ------------------------------
@csrf_exempt
def delete_orders(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            for order in Order.objects.filter(order_id__in=data.get("ids", [])):
                order.delete()  # Soft delete
                try:
                    log_audit(
                        user=request.user if request.user.is_authenticated else None,
                        action="delete",
                        instance=order,
                        changes={"deleted": True},
                        request=request,
                    )
                except Exception:
                    pass
            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request method"})


@csrf_exempt
def permanently_delete_orders(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            for order in Order.objects.filter(
                order_id__in=data.get("ids", []), is_deleted=True
            ):
                try:
                    log_audit(
                        user=request.user if request.user.is_authenticated else None,
                        action="delete",
                        instance=order,
                        changes={"permanently_deleted": True},
                        request=request,
                    )
                except Exception:
                    pass
                order.delete()
            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request method"})


@csrf_exempt
def restore_orders(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            for order in Order.objects.filter(
                order_id__in=data.get("ids", []), is_deleted=True
            ):
                order.restore()
                try:
                    log_audit(
                        user=request.user if request.user.is_authenticated else None,
                        action="update",
                        instance=order,
                        changes={"restored": True},
                        request=request,
                    )
                except Exception:
                    pass
            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request method"})


# ------------------------------
# ORDER DETAILS API
# ------------------------------
@login_required
def order_details_api(request, order_id):
    """
    API endpoint for order details.
    UPDATED: Returns complete order information including GCash reference image URL.
    """
    try:
        order = (
            Order.objects.select_related("customer__customer_profile")
            .prefetch_related("items__product_variant__product")
            .get(id=order_id, is_deleted=False)
        )

        # Build items list with proper field names
        items = []
        for item in order.items.all():
            items.append(
                {
                    "product_name": item.product_variant.product.name,
                    "variant": (
                        item.product_variant.name
                        if hasattr(item.product_variant, "name")
                        else "Default"
                    ),
                    "quantity": item.quantity,
                    "price": float(item.price_at_order),
                    "total": float(item.item_total),
                }
            )

        # Determine the image URL for the API response
        gcash_image_url = None
        if order.gcash_reference_image:
            gcash_image_url = order.gcash_reference_image.url

        # Get customer profile for address information
        profile = (
            order.customer.customer_profile
            if hasattr(order.customer, "customer_profile")
            else None
        )

        # Build shipping address from profile
        shipping_address = "No address on file"
        shipping_phone = "No phone on file"
        if profile:
            address_parts = []
            if profile.street_address:
                address_parts.append(profile.street_address)
            if profile.city:
                address_parts.append(profile.city)
            if profile.province:
                address_parts.append(profile.province)
            if profile.zip_code:
                address_parts.append(profile.zip_code)

            if address_parts:
                shipping_address = ", ".join(address_parts)

            if profile.phone:
                shipping_phone = profile.phone

        return JsonResponse(
            {
                "id": order.id,
                "order_id": order.order_id,
                "customer": {
                    "name": (
                        order.customer.get_full_name() if order.customer else "Guest"
                    ),
                    "username": order.customer.username if order.customer else "Guest",
                    "email": order.customer.email if order.customer else "No email",
                },
                # Add flat customer fields for easier access
                "customer_name": (
                    order.customer.get_full_name() if order.customer else "Guest"
                ),
                "customer_email": (
                    order.customer.email if order.customer else "No email"
                ),
                "status": order.status,
                "order_date": order.order_date.isoformat(),
                "expected_delivery_date": (
                    order.expected_delivery_date.isoformat()
                    if order.expected_delivery_date
                    else None
                ),
                "total_amount": float(order.get_total_cost),
                "total_cost": float(order.get_total_cost),  # Add this for compatibility
                "payment_method": order.payment_method,
                "payment_status": order.payment_status,
                "payment_verified_at": order.payment_verified_at.isoformat() if order.payment_verified_at else None,
                "gcash_reference_image_url": gcash_image_url,
                # Include the GCash image URL
                "gcash_reference_image_url": gcash_image_url,
                # Address information
                "shipping_address": shipping_address,
                "shipping_phone": shipping_phone,
                "billing_address": shipping_address,  # Using same as shipping since that's how your checkout works
                "items": items,
                "is_deleted": order.is_deleted,
                "deleted_at": (
                    order.deleted_at.isoformat() if order.deleted_at else None
                ),
            }
        )

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        # Log the full traceback for debugging
        logger.error(
            f"Error in order_details_api for order ID {order_id}: {traceback.format_exc()}"
        )
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)


# ------------------------------
# UPDATE ORDER STATUS
# ------------------------------


# ============================================
# UPDATED STATUS UPDATE WITH ROLE CHECKS
# ============================================
# ============================================
# UPDATED STATUS UPDATE WITH ROLE CHECKS
# CHANGE: Admin/Manager can now also approve Processing → Shipped
# ============================================
@csrf_exempt
@login_required
def update_order_status(request, order_id):
    """
    Role-based order status updates with validation
    UPDATED: Admin/Manager can now approve both Pending → Processing AND Processing → Shipped
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid request method"})

    try:
        data = json.loads(request.body)
        new_status = data.get("status")

        if new_status not in dict(Order.ORDER_STATUS_CHOICES):
            return JsonResponse({"success": False, "error": "Invalid status"})

        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            current_status = order.status
            user_role = request.user.role

            # ============================================
            # ROLE-BASED STATUS TRANSITION VALIDATION
            # ============================================

            # ADMIN/MANAGER: Full control over order workflow
            if user_role in ["admin", "manager"]:

                # Normal workflow: Pending → Processing
                if current_status == "Pending" and new_status == "Processing":
                    order.status = new_status
                    order.approved_by = request.user
                    order.approved_at = timezone.now()
                    
                    # Set expected_delivery_date if provided
                    expected_delivery_date = data.get("expected_delivery_date")
                    if expected_delivery_date:
                        try:
                            # Parse the date string (format: YYYY-MM-DD)
                            order.expected_delivery_date = datetime.strptime(expected_delivery_date, "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            # If date parsing fails, continue without setting it
                            pass
                    
                    order.save()

                    # Generate PDF invoice and send email
                    pdf_buffer = generate_invoice_pdf(order)
                    email_subject = f"Invoice for your Order {order.order_id}"
                    email_body = (
                        f"Hi {order.customer.first_name} {order.customer.last_name},\n\n"
                        "Thank you for your purchase! Please find your invoice attached.\n\n"
                        "Best regards,\nSupplyTrack Team"
                    )
                    email = EmailMessage(
                        subject=email_subject,
                        body=email_body,
                        from_email="SupplyTrack <danegela13@gmail.com>",
                        to=[order.customer.email, "danegela13@gmail.com"],
                    )
                    email.attach(f"Invoice_{order.order_id}.pdf", pdf_buffer.getvalue(), "application/pdf")
                    email.send(fail_silently=True)

                # NEW: Admin/Manager can also approve Processing → Shipped
                elif current_status == "Processing" and new_status == "Shipped":
                    order.status = new_status
                    order.shipped_by = request.user
                    order.shipped_at = timezone.now()
                    order.save()

                # Cancel any active order
                elif new_status == "Canceled" and current_status not in [
                    "Completed",
                    "Returned",
                ]:
                    order.status = new_status
                    order.save()

                # REACTIVATION LOGIC
                # Reactivate Canceled order → Back to Pending for re-approval
                elif current_status == "Canceled" and new_status == "Pending":
                    # Check if stock was restored during cancellation
                    if order.stock_restored:
                        # Need to re-deduct stock
                        insufficient_stock_errors = []

                        for item in order.items.select_related(
                            "product_variant__product"
                        ).all():
                            product = Product.objects.select_for_update().get(
                                pk=item.product_variant.product.pk
                            )

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
                            error_msg = "; ".join(insufficient_stock_errors)
                            logger.error(
                                f"Cannot reactivate Order {order.order_id}: {error_msg}"
                            )
                            return JsonResponse(
                                {
                                    "success": False,
                                    "error": f"Cannot reactivate: {error_msg}",
                                }
                            )

                        # Successfully re-deducted stock
                        order.stock_restored = False
                        order.stock_restored_at = None
                        order.stock_deducted_at = timezone.now()

                    order.status = "Pending"
                    order.save()

                # Reactivate Returned order → Back to Pending
                elif current_status == "Returned" and new_status == "Pending":
                    # Same logic as Canceled reactivation
                    if order.stock_restored:
                        insufficient_stock_errors = []

                        for item in order.items.select_related(
                            "product_variant__product"
                        ).all():
                            product = Product.objects.select_for_update().get(
                                pk=item.product_variant.product.pk
                            )

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
                            error_msg = "; ".join(insufficient_stock_errors)
                            return JsonResponse(
                                {
                                    "success": False,
                                    "error": f"Cannot reactivate: {error_msg}",
                                }
                            )

                        order.stock_restored = False
                        order.stock_restored_at = None
                        order.stock_deducted_at = timezone.now()

                    order.status = "Pending"
                    order.save()

                else:
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Cannot change order from {current_status} to {new_status}",
                        }
                    )

            # STAFF: Can only update Processing → Shipped
            elif user_role == "staff":
                if current_status == "Processing" and new_status == "Shipped":
                    order.status = new_status
                    order.shipped_by = request.user
                    order.shipped_at = timezone.now()
                    order.save()
                else:
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Staff can only change Processing orders to Shipped. Current status: {current_status}",
                        }
                    )

            # DELIVERY: Can update delivery statuses
            elif user_role == "delivery":
                if current_status == "Shipped" and new_status in [
                    "Out for Delivery",
                    "Delivered",
                    "Failed",
                ]:
                    order.status = new_status
                    order.delivery_handler = request.user
                    if new_status == "Delivered":
                        order.delivered_at = timezone.now()
                    order.save()
                else:
                    return JsonResponse(
                        {
                            "success": False,
                            "error": "Delivery personnel can only update shipped orders",
                        }
                    )

            else:
                return JsonResponse(
                    {
                        "success": False,
                        "error": "You do not have permission to update order status",
                    }
                )

            # Calculate total cost
            from django.db.models import F, Sum, ExpressionWrapper, DecimalField

            total_cost_db = (
                Order.objects.filter(id=order_id)
                .annotate(
                    total_cost_db=Sum(
                        ExpressionWrapper(
                            F("items__price_at_order") * F("items__quantity"),
                            output_field=DecimalField(max_digits=20, decimal_places=2),
                        )
                    )
                )
                .values_list("total_cost_db", flat=True)
                .first()
                or 0
            )

            # Audit log
            try:
                log_audit(
                    user=request.user,
                    action="update",
                    instance=order,
                    changes={"status": [current_status, order.status]},
                    request=request,
                )
            except Exception:
                pass

            return JsonResponse(
                {
                    "success": True,
                    "order_id": order.order_id,
                    "old_status": current_status,
                    "new_status": order.status,
                    "total_cost": float(total_cost_db),
                    "message": f"Order status updated from {current_status} to {order.status}",
                }
            )

    except Order.DoesNotExist:
        return JsonResponse({"success": False, "error": "Order not found"})
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON data"})
    except Exception as e:
        logger.error(f"Error updating order status: {traceback.format_exc()}")
        return JsonResponse({"success": False, "error": str(e)})
# ============================================
# PERMISSION DENIED VIEW
# ============================================


def permission_denied_view(request):
    """Handle permission denied cases"""
    return render(
        request,
        "errors/permission_denied.html",
        {"message": "You do not have permission to access this page."},
    )


# ------------------------------
# CHECKOUT - REFACTORED
# ------------------------------
from django.contrib.auth.decorators import login_required
import logging

logger = logging.getLogger(__name__)


@transaction.atomic
@login_required
def checkout_view(request):
    """
    ONE-STEP CHECKOUT WITH AUTOFILL PAYMENT AND PROFILE ADDRESSES.
    - Uses customer's profile for shipping & billing.
    - Payment method defaults to COD but can be changed in the form.
    - Validates stock availability.
    """
    # Validate user has CustomerProfile with complete address
    try:
        profile = request.user.customer_profile
        if (
            not profile.street_address
            or not profile.city
            or not profile.province
            or not profile.zip_code
        ):
            messages.error(
                request,
                "⚠️ Please complete your address information before checking out.",
            )
            return redirect("users:profile_edit")
    except Exception:
        messages.error(
            request,
            "⚠️ You need a customer profile with address information to checkout.",
        )
        return redirect("users:profile_edit")

    cart = get_or_create_cart(request)

    if request.method == "POST":
        form = CheckoutForm(request.POST, request.FILES)
        if form.is_valid():
            payment_method = form.cleaned_data.get("payment_method", "COD")
            try:
                with transaction.atomic():
                    cart_items = list(
                        cart.items.select_related("product_variant__product").all()
                    )
                    if not cart_items:
                        messages.error(request, "Your cart is empty.")
                        return redirect("store:cart_view")

                    # Validate stock
                    products_to_update = []
                    errors = []
                    for cart_item in cart_items:
                        product = Product.objects.select_for_update().get(
                            pk=cart_item.product_variant.product.pk
                        )
                        if product.stock_quantity < cart_item.quantity:
                            errors.append(
                                f"{product.name}: Need {cart_item.quantity}, only {product.stock_quantity} available"
                            )
                        else:
                            products_to_update.append(
                                {
                                    "product": product,
                                    "quantity": cart_item.quantity,
                                    "cart_item": cart_item,
                                }
                            )

                    if errors:
                        for error in errors:
                            messages.error(request, f"⚠️ Insufficient stock: {error}")
                        return redirect("store:cart_view")
                    initial_status = (
                        "Pending" if payment_method == "GCASH" else "Pending"
                    )

                    # Create Order
                    order = Order(
                        customer=request.user,
                        payment_method=payment_method,
                        gcash_reference_image=form.cleaned_data.get(
                            "gcash_reference_image"
                        ),
                        status=initial_status,
                    )
                    order.save()
                    if payment_method == "GCASH":
                        messages.warning(
                            request,
                            f"Order #{order.order_id} placed! It is **Pending** payment verification. We'll confirm shortly.",
                        )

                    # Create order items & deduct stock
                    order_items = []
                    for item_data in products_to_update:
                        product = item_data["product"]
                        cart_item = item_data["cart_item"]
                        quantity = item_data["quantity"]
                        price_to_use = (
                            cart_item.product_variant.price
                            or product.price
                            or Decimal("0.00")
                        )

                        order_items.append(
                            OrderItem(
                                order=order,
                                product_variant=cart_item.product_variant,
                                quantity=quantity,
                                price_at_order=price_to_use,
                            )
                        )

                        # Deduct stock
                        product.stock_quantity -= quantity
                        product.save()
                        StockMovement.objects.create(
                            product=product, movement_type="OUT", quantity=quantity
                        )

                    OrderItem.objects.bulk_create(order_items)
                    order.stock_deducted = True
                    order.stock_deducted_at = timezone.now()
                    order.save()

                    # Clear cart
                    cart.items.all().delete()
                    cart.delete()

                    # Send confirmation email
                    try:
                        from .views import send_order_confirmation_email_to_customer

                        send_order_confirmation_email_to_customer(order)
                    except Exception as e:
                        logger.error(f"Failed to send confirmation email: {e}")

                    # after order saved and items created...
                    try:
                        log_audit(
                            user=request.user,
                            action="create",
                            instance=order,
                            changes={
                                "order_created": True,
                                "total": float(order.get_total_cost),
                            },
                            request=request,
                        )
                    except Exception:
                        pass
                    messages.success(
                        request, f"Order #{order.order_id} placed successfully!"
                    )
                    return redirect("orders:order_confirmation", order_id=order.id)

            except Product.DoesNotExist:
                messages.error(request, "One or more products are no longer available.")
                return redirect("store:cart_view")
            except Exception as e:
                logger.error(f"Checkout error: {e}")
                messages.error(request, "An error occurred during checkout.")
                return redirect("store:cart_view")
        else:
            # Show form errors as messages
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")

    else:
        # GET request: prefill payment method
        form = CheckoutForm(initial={"payment_method": "COD"})

    # Calculate cart total
    cart_total = Decimal("0.00")
    for item in cart.items.all():
        price = getattr(item.product_variant, "price", None) or getattr(
            item.product_variant.product, "price", Decimal("0.00")
        )
        cart_total += price * item.quantity

    context = {
        "form": form,
        "cart": cart,
        "cart_total": cart_total,
        "customer_profile": profile,
    }

    return render(request, "orders/checkout.html", context)


# ------------------------------
# ORDER CONFIRMATION
# ------------------------------
@login_required
def order_confirmation_view(request, order_id):
    """
    UPDATED: Order confirmation now uses addresses from CustomerProfile.
    """
    order = get_object_or_404(
        Order.objects.select_related("customer__customer_profile"), id=order_id
    )

    if order.customer and request.user != order.customer:
        messages.error(request, "You do not have permission to view this order.")
        return redirect("orders:my_orders")

    order_items = order.items.select_related("product_variant__product").all()
    delivery_note = None
    if hasattr(order, "delivery"):
        delivery_note = order.delivery.delivery_note


    context = {
        "order": order,
        "order_items": order_items,
        "delivery_note": delivery_note,  # ✅ Added here

        "page_title": f"Order #{order.order_id} Confirmation",
        "status_badge_class": {
            "Pending": "badge-warning",
            "Processing": "badge-info",
            "Shipped": "badge-primary",
            "Completed": "badge-success",
            "Canceled": "badge-danger",
            "Returned": "badge-secondary",
        },
    }
    return render(request, "orders/order_confirmation.html", context)


# ------------------------------
# MY ORDERS (FOR LOGGED-IN USER)
# ------------------------------
@login_required
def my_orders_view(request):
    """
    UPDATED: Prefetch customer_profile for efficient address display.
    """
    orders = (
        Order.objects.filter(customer=request.user)
        .select_related("customer__customer_profile")
        .order_by("-order_date")
        if request.user.is_authenticated
        else Order.objects.none()
    )

    return render(
        request, "orders/my_orders.html", {"orders": orders, "page_title": "My Orders"}
    )


# ------------------------------
# MANUAL ORDER CRUD (UNCHANGED - keeps address fields)
# ------------------------------
@login_required
def manual_order_list(request):
    orders = (
        ManualOrder.objects.select_related("created_by")
        .prefetch_related("items__product")
        .order_by("-created_at")
    )
    return render(request, "orders/manual_orders_list.html", {"orders": orders})


@login_required
def manual_order_create(request):
    form = ManualOrderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        manual_order = form.save(commit=False)
        manual_order.created_by = request.user
        manual_order.save()
        form.save_m2m()
        messages.success(request, "Manual order created successfully!")

        # schedule audit log after DB commit so it won't run if transaction rolls back
        def _log_manual_order_created():
            try:
                log_audit(
                    user=request.user,
                    action="create",
                    instance=manual_order,
                    changes={"manual_order_created": True},
                    request=request,
                )
            except Exception:
                pass

        transaction.on_commit(_log_manual_order_created)

        return redirect("orders:manual_order_list")
    return render(request, "orders/manual_order_form.html", {"form": form})


@login_required
def manual_order_delete(request, pk):
    order = get_object_or_404(ManualOrder, pk=pk)
    order.delete()
    try:
        log_audit(
            user=request.user,
            action="delete",
            instance=order,
            changes={"manual_order_deleted": True},
            request=request,
        )
    except Exception:
        pass
    messages.success(request, "Manual order deleted successfully!")
    return redirect("orders:manual_order_list")
# billing dashboard
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum, Q, F
from django.utils import timezone
from decimal import Decimal

# Assuming Order and ManualOrder models are imported
# from .models import Order, ManualOrder 

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum, Q, F, Count
from django.utils import timezone
from decimal import Decimal

# Assuming Order, ManualOrder models are imported here.
# NOTE: Ensure the function 'sum_queryset_costs' is defined 
# or use Python aggregation on the properties as shown below.

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal

# Assuming Order, ManualOrder models are imported

@login_required
def billing_dashboard(request):
    """
    Billing dashboard with unified table view.
    """
    
    today = timezone.now().date()
    
    # Helper function
    def get_total_cost_sum(queryset):
        return sum(o.get_total_cost for o in list(queryset))

    # --- Fetch all orders ---
    orders = Order.objects.filter(is_deleted=False).select_related('customer').order_by('-order_date')
    manual_orders = ManualOrder.objects.filter(is_deleted=False).select_related('created_by').order_by('-created_at')
    
    # Combine and sort by date
    all_orders_list = list(orders) + list(manual_orders)
    all_orders_list.sort(
        key=lambda x: x.order_date if hasattr(x, 'order_date') else x.created_at, 
        reverse=True
    )

    # --- KPI Calculations ---
    paid_orders = orders.filter(payment_status='paid')
    paid_manual = manual_orders.filter(payment_status='paid')
    paid_total_revenue = get_total_cost_sum(paid_orders) + get_total_cost_sum(paid_manual)

    unpaid_orders = orders.filter(payment_status='unpaid')
    unpaid_manual = manual_orders.filter(payment_status='unpaid')
    unpaid_total_amount = get_total_cost_sum(unpaid_orders) + get_total_cost_sum(unpaid_manual)

    refunded_orders = orders.filter(payment_status__in=['refunded', 'partially_refunded'])
    refunded_manual = manual_orders.filter(payment_status__in=['refunded', 'partially_refunded'])
    refunded_total_amount = get_total_cost_sum(refunded_orders) + get_total_cost_sum(refunded_manual)

    gcash_pending_count = (
        orders.filter(payment_method='GCASH', status='Pending', payment_status='unpaid').count() +
        manual_orders.filter(payment_method='GCASH', status='Pending', payment_status='unpaid').count()
    )

    paid_today_count = (
        orders.filter(payment_status='paid', payment_verified_at__date=today).count() +
        manual_orders.filter(payment_status='paid', payment_verified_at__date=today).count()
    )

    context = {
        # KPIs
        'paid_total_revenue': paid_total_revenue,
        'unpaid_total_amount': unpaid_total_amount,
        'refunded_total_amount': refunded_total_amount,
        'gcash_pending_count': gcash_pending_count,
        'paid_today_count': paid_today_count,
        
        # Unified order list
        'all_orders': all_orders_list,
    }

    return render(request, 'orders/billing_dashboard.html', context)
from django.shortcuts import get_object_or_404

@login_required
def billing_order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("customer__customer_profile")
        .prefetch_related("items__product_variant__product"),
        id=order_id,
        is_deleted=False,
    )

    # Items
    items = [{
        "product_name": item.product_variant.product.name,
        "variant": getattr(item.product_variant, "name", "Default"),
        "quantity": item.quantity,
        "price": float(item.price_at_order),
        "total": float(item.item_total),
    } for item in order.items.all()]

    # Profile -> shipping
    profile = getattr(order.customer, "customer_profile", None)
    shipping_address = "No address on file"
    shipping_phone = "No phone on file"
    if profile:
        parts = [p for p in [profile.street_address, profile.city, profile.province, profile.zip_code] if p]
        if parts: shipping_address = ", ".join(parts)
        if profile.phone: shipping_phone = profile.phone

    gcash_image_url = order.gcash_reference_image.url if order.gcash_reference_image else None

    context = {
        "page_type": "order",
        "order_id": order.order_id,
        "customer_name": order.customer.get_full_name() if order.customer else "Guest",
        "customer_email": order.customer.email if order.customer else "No email",
        "status": order.status,
        "payment_method": order.payment_method,
        "payment_status": getattr(order, "payment_status", "unpaid"),
        "payment_verified_at": order.payment_verified_at,
        "order_date": order.order_date,
        "total_cost": float(order.get_total_cost),
        "shipping_address": shipping_address,
        "shipping_phone": shipping_phone,
        "gcash_reference_image_url": gcash_image_url,
        "items": items,
    }
    return render(request, "orders/billing_order_detail.html", context)

@login_required
def billing_manual_order_detail(request, order_id):
    order = get_object_or_404(
        ManualOrder.objects.select_related("created_by")
        .prefetch_related("items__product_variant__product"),
        id=order_id,
        is_deleted=False,
    )

    items = [{
        "product_name": item.product_variant.product.name,
        "variant": getattr(item.product_variant, "name", getattr(item.product_variant, "sku", "Default")),
        "quantity": item.quantity,
        "price": float(item.price_at_order),
        "total": float(item.item_total),
    } for item in order.items.all()]

    gcash_image_url = order.gcash_reference_image.url if getattr(order, "gcash_reference_image", None) else None

    context = {
        "page_type": "manual",
        "order_id": order.manual_order_id,
        "customer_name": order.get_customer_display(),
        "customer_email": order.customer_email or "N/A",
        "status": order.status,
        "payment_method": order.get_payment_method_display(),
        "payment_status": getattr(order, "payment_status", "unpaid"),
        "payment_verified_at": order.payment_verified_at,
        "order_date": order.order_date or order.created_at,
        "total_cost": float(order.get_total_cost),
        "shipping_address": order.shipping_address,
        "shipping_phone": order.customer_phone or "N/A",
        "gcash_reference_image_url": gcash_image_url,
        "items": items,
    }
    return render(request, "orders/billing_order_detail.html", context)
# Add these views to apps/orders/views.py

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from .models import Order, ManualOrder
import logging

logger = logging.getLogger(__name__)


@login_required
def customer_invoice_view(request, order_id, order_type):
    """
    Display invoice for a customer order (view-only, auto-filled)
    order_type: 'order' for regular orders, 'manual' for manual orders
    """
    
    if order_type == 'order':
        order = get_object_or_404(
            Order.objects.select_related('customer__customer_profile')
            .prefetch_related('items__product_variant__product'),
            id=order_id,
            is_deleted=False
        )
        order_number = order.order_id
        order_date = order.order_date
        customer_name = order.get_customer_name()
        customer_email = order.get_customer_email()
        customer_phone = order.get_customer_phone()
        shipping_address = order.get_shipping_address()
        
    elif order_type == 'manual':
        order = get_object_or_404(
            ManualOrder.objects.select_related('customer', 'created_by')
            .prefetch_related('items__product_variant__product'),
            id=order_id,
            is_deleted=False
        )
        order_number = order.manual_order_id
        order_date = order.order_date or order.created_at
        customer_name = order.get_customer_display()
        customer_email = order.customer_email or 'N/A'
        customer_phone = order.customer_phone or 'N/A'
        shipping_address = order.shipping_address
        
    else:
        return HttpResponse("Invalid order type", status=400)
    
    # Get order items
    items = order.items.select_related('product_variant__product').all()
    
    # Calculate totals
    subtotal = order.get_total_cost
    tax = 0  # Add tax calculation if needed
    total = subtotal + tax
    
    context = {
        'order': order,
        'order_number': order_number,
        'order_date': order_date,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'shipping_address': shipping_address,
        'items': items,
        'subtotal': subtotal,
        'tax': tax,
        'total': total,
        'payment_method': order.payment_method,
        'payment_status': order.payment_status,
        'order_status': order.status,
        'order_type': order_type,
        'page_title': f'Invoice - {order_number}'
    }
    
    return render(request, 'orders/customer_invoice.html', context)

from django.template.loader import get_template
from django.http import HttpResponse
from xhtml2pdf import pisa
import io

@login_required
def customer_invoice_pdf(request, order_id, order_type):
    """
    Generate downloadable PDF invoice for customer orders (manual or regular)
    """
    # --- Fetch order details (same logic as before) ---
    if order_type == 'order':
        order = get_object_or_404(
            Order.objects.select_related('customer__customer_profile')
            .prefetch_related('items__product_variant__product'),
            id=order_id,
            is_deleted=False
        )
        order_number = order.order_id
        order_date = order.order_date
        customer_name = order.get_customer_name()
        customer_email = order.get_customer_email()
        customer_phone = order.get_customer_phone()
        shipping_address = order.get_shipping_address()

    elif order_type == 'manual':
        order = get_object_or_404(
            ManualOrder.objects.select_related('customer', 'created_by')
            .prefetch_related('items__product_variant__product'),
            id=order_id,
            is_deleted=False
        )
        order_number = order.manual_order_id
        order_date = order.order_date or order.created_at
        customer_name = order.get_customer_display()
        customer_email = order.customer_email or 'N/A'
        customer_phone = order.customer_phone or 'N/A'
        shipping_address = order.shipping_address
    else:
        return HttpResponse("Invalid order type", status=400)

    items = order.items.select_related('product_variant__product').all()
    subtotal = order.get_total_cost
    tax = 0
    total = subtotal + tax

    context = {
        'order': order,
        'order_number': order_number,
        'order_date': order_date,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'shipping_address': shipping_address,
        'items': items,
        'subtotal': subtotal,
        'tax': tax,
        'total': total,
        'payment_method': order.payment_method,
        'payment_status': order.payment_status,
        'order_status': order.status,
        'is_pdf': True,  # optional flag for styling differences
    }

    # --- Generate PDF ---
    template = get_template('orders/customer_invoice_pdf.html')
    html = template.render(context)
    source_html = html.encode("UTF-8")  # Encode the string once
    result = io.BytesIO()
    # Pass the bytes object to pisaDocument
    pdf = pisa.pisaDocument(io.BytesIO(source_html), result)

    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        filename = f"Invoice_{order_number}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        return HttpResponse("Error generating PDF", status=500)

#For Invoice
def generate_invoice_pdf(order):
    """Generate downloadable PDF invoice for a given order"""
    
    # --- Order Details ---
    order_number = getattr(order, "order_id", getattr(order, "manual_order_id", "N/A"))
    order_date = getattr(order, "order_date", getattr(order, "created_at", None))
    customer_name = f"{order.customer.first_name} {order.customer.last_name}" if hasattr(order, "customer") else "N/A"
    customer_email = getattr(order.customer, "email", "N/A")
    customer_phone = getattr(order.customer.customer_profile, "phone", "N/A") if hasattr(order.customer, "customer_profile") else "N/A"
    shipping_address = getattr(order, "shipping_address", "N/A")
    items = order.items.select_related("product_variant__product").all()
    
    # --- Calculate totals ---
    subtotal = sum([item.quantity * float(item.price_at_order) for item in items])
    tax = 0
    total = subtotal + tax

    # --- Determine payment status ---
    payment_method = order.payment_method if order.payment_method else "N/A"
    if payment_method.upper() == "GCASH" and order.status in ["Processing", "Shipped", "Completed"]:
        payment_status = "Paid"
    else:
        payment_status = "Unpaid"

    # --- Context for HTML template ---
    context = {
        "order": order,
        "order_number": order_number,
        "order_date": order_date,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "customer_phone": customer_phone,
        "shipping_address": shipping_address,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "order_status": getattr(order, "status", "N/A"),
        "is_pdf": True,
    }

    # --- Render PDF from template ---
    template = get_template("orders/customer_invoice_pdf.html")
    html = template.render(context)
    source_html = html.encode("UTF-8")  # encode once

    result = io.BytesIO()
    pdf = pisa.pisaDocument(io.BytesIO(source_html), result)
    if not pdf.err:
        response = HttpResponse(result.getvalue(), content_type="application/pdf")
        filename = f"Invoice_{order_number}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    else:
        return HttpResponse("Error generating PDF", status=500)
# cateogory list view
# ==============================================================================
# CATEGORY VIEW
# ==============================================================================
from django.shortcuts import render, get_object_or_404
from apps.inventory.models import Category, Product


@login_required
def delivery_calendar_view(request):
    """Calendar view showing delivery dates and payment due dates"""
    if not is_staff_or_above(request.user):
        return redirect('orders:my_orders')
    
    return render(request, 'orders/delivery_calendar.html')


@login_required
def calendar_events_api(request):
    """API endpoint to fetch calendar events (deliveries and payment due dates)"""
    if not is_staff_or_above(request.user):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    from datetime import datetime, date, time
    from apps.purchase_orders.models import PurchaseOrder
    
    events = []
    
    # Get delivery dates from Customer Orders
    customer_orders = Order.objects.filter(
        is_deleted=False,
        expected_delivery_date__isnull=False
    ).exclude(status__in=['Canceled', 'Returned'])
    
    for order in customer_orders:
        if order.expected_delivery_date:
            # Convert date to datetime with time (9:00 AM default)
            delivery_datetime = datetime.combine(order.expected_delivery_date, time(9, 0))
            events.append({
                'title': f'📦 Delivery: {order.order_id}',
                'start': delivery_datetime.isoformat(),
                'allDay': False,
                'type': 'delivery',
                'order_type': 'customer_order',
                'order_id': order.id,
                'order_number': order.order_id,
                'customer': order.customer.username,
                'status': order.status,
                'color': '#28a745',  # Green for deliveries
                'textColor': '#fff',
            })
    
    # Get order creation dates from Customer Orders
    customer_orders_created = Order.objects.filter(
        is_deleted=False
    ).exclude(status__in=['Canceled', 'Returned'])
    
    for order in customer_orders_created:
        if order.order_date:
            # Use the actual order_date datetime if available, otherwise combine with time
            if isinstance(order.order_date, datetime):
                order_datetime = order.order_date
            else:
                order_date = order.order_date.date() if hasattr(order.order_date, 'date') else order.order_date
                order_datetime = datetime.combine(order_date, time(8, 0))  # 8:00 AM
            events.append({
                'title': f'📝 Order Created: {order.order_id}',
                'start': order_datetime.isoformat(),
                'allDay': False,
                'type': 'order_created',
                'order_type': 'customer_order',
                'order_id': order.id,
                'order_number': order.order_id,
                'customer': order.customer.username,
                'status': order.status,
                'color': '#6c757d',  # Gray for order creation
                'textColor': '#fff',
                'borderColor': '#495057',
            })
    
    # Get delivery dates from Manual Orders
    manual_orders = ManualOrder.objects.filter(
        is_deleted=False,
        expected_delivery_date__isnull=False
    ).exclude(status__in=['Canceled', 'Returned'])
    
    for order in manual_orders:
        if order.expected_delivery_date:
            customer_name = order.customer_name if order.customer_name else (order.customer.username if order.customer else 'Manual Order')
            # Convert date to datetime with time (10:00 AM default)
            delivery_datetime = datetime.combine(order.expected_delivery_date, time(10, 0))
            events.append({
                'title': f'📦 Delivery: {order.manual_order_id}',
                'start': delivery_datetime.isoformat(),
                'allDay': False,
                'type': 'delivery',
                'order_type': 'manual_order',
                'order_id': order.id,
                'order_number': order.manual_order_id,
                'customer': customer_name,
                'status': order.status,
                'color': '#17a2b8',  # Blue for manual orders
                'textColor': '#fff',
            })
    
    # Get order creation dates from Manual Orders
    manual_orders_created = ManualOrder.objects.filter(
        is_deleted=False
    ).exclude(status__in=['Canceled', 'Returned'])
    
    for order in manual_orders_created:
        if order.order_date:
            # Use the actual order_date datetime if available, otherwise combine with time
            if isinstance(order.order_date, datetime):
                order_datetime = order.order_date
            else:
                order_date = order.order_date.date() if hasattr(order.order_date, 'date') else order.order_date
                order_datetime = datetime.combine(order_date, time(8, 30))  # 8:30 AM
            customer_name = order.customer_name if order.customer_name else (order.customer.username if order.customer else 'Manual Order')
            events.append({
                'title': f'📝 Manual Order Created: {order.manual_order_id}',
                'start': order_datetime.isoformat(),
                'allDay': False,
                'type': 'order_created',
                'order_type': 'manual_order',
                'order_id': order.id,
                'order_number': order.manual_order_id,
                'customer': customer_name,
                'status': order.status,
                'color': '#6c757d',  # Gray for order creation
                'textColor': '#fff',
                'borderColor': '#495057',
            })
    
    # Get delivery dates from Purchase Orders
    purchase_orders = PurchaseOrder.objects.filter(
        is_deleted=False,
        expected_delivery_date__isnull=False
    ).exclude(status__in=['cancelled', 'refund'])
    
    for po in purchase_orders:
        if po.expected_delivery_date:
            supplier_name = po.supplier_profile.company_name if po.supplier_profile else 'No Supplier'
            # Convert date to datetime with time (11:00 AM default)
            delivery_datetime = datetime.combine(po.expected_delivery_date, time(11, 0))
            events.append({
                'title': f'📦 PO Delivery: {po.purchase_order_id}',
                'start': delivery_datetime.isoformat(),
                'allDay': False,
                'type': 'delivery',
                'order_type': 'purchase_order',
                'order_id': po.id,
                'order_number': po.purchase_order_id,
                'supplier': supplier_name,
                'status': po.status,
                'color': '#ffc107',  # Yellow/Orange for PO deliveries
                'textColor': '#000',
            })
    
    # Get order creation dates from Purchase Orders
    purchase_orders_created = PurchaseOrder.objects.filter(
        is_deleted=False
    ).exclude(status__in=['cancelled', 'refund'])
    
    for po in purchase_orders_created:
        if po.order_date:
            # Use the actual order_date datetime if available, otherwise combine with time
            if isinstance(po.order_date, datetime):
                order_datetime = po.order_date
            else:
                order_date = po.order_date.date() if hasattr(po.order_date, 'date') else po.order_date
                order_datetime = datetime.combine(order_date, time(7, 0))  # 7:00 AM (early morning for PO creation)
            supplier_name = po.supplier_profile.company_name if po.supplier_profile else 'No Supplier'
            events.append({
                'title': f'📝 PO Created: {po.purchase_order_id}',
                'start': order_datetime.isoformat(),
                'allDay': False,
                'type': 'order_created',
                'order_type': 'purchase_order',
                'order_id': po.id,
                'order_number': po.purchase_order_id,
                'supplier': supplier_name,
                'status': po.status,
                'color': '#6c757d',  # Gray for order creation
                'textColor': '#fff',
                'borderColor': '#495057',
            })
    
    # Get payment due dates from Purchase Orders (net_30 or pay_later)
    payment_due_pos = PurchaseOrder.objects.filter(
        is_deleted=False,
        payment_due_date__isnull=False,
        payment_status__in=['unpaid', 'overdue']
    ).filter(
        Q(payment_method='net_30') | Q(pay_later=True)
    )
    
    for po in payment_due_pos:
        if po.payment_due_date:
            supplier_name = po.supplier_profile.company_name if po.supplier_profile else 'No Supplier'
            # Determine if overdue
            today = date.today()
            is_overdue = po.payment_due_date < today
            # Convert date to datetime with time (2:00 PM default for payments)
            payment_datetime = datetime.combine(po.payment_due_date, time(14, 0))
            
            events.append({
                'title': f'💰 Payment Due: {po.purchase_order_id}',
                'start': payment_datetime.isoformat(),
                'allDay': False,
                'type': 'payment_due',
                'order_type': 'purchase_order',
                'order_id': po.id,
                'order_number': po.purchase_order_id,
                'supplier': supplier_name,
                'amount': str(po.total_cost),
                'payment_method': po.get_payment_method_display(),
                'is_overdue': is_overdue,
                'color': '#dc3545' if is_overdue else '#fd7e14',  # Red if overdue, orange if due soon
                'textColor': '#fff',
            })
    
    return JsonResponse(events, safe=False)


