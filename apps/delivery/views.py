# delivery.views.py
from django.shortcuts import render, get_object_or_404, redirect
from .models import Delivery
from .forms import ProofOfDeliveryForm
from apps.orders.models import Order, OrderItem
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from apps.transactions.models import log_audit  # added

from django.db.models import Prefetch
from django.urls import reverse
from django.db import transaction

import json
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone


def send_delivery_status_update_email(delivery):
    """
    Minimal safe email notifier — returns silently if missing config/email.
    """
    try:
        customer = getattr(delivery.order, "customer", None)
        if not customer or not getattr(customer, "email", None):
            return
        subject = f"Delivery Update for Order {delivery.order.order_id}"
        message = f"Hello {getattr(customer, 'username', '')},\n\nDelivery status: {delivery.get_delivery_status_display()}.\n\nThanks,\nSupplyTrack"
        send_mail(
            subject,
            message,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [customer.email],
            fail_silently=True,
        )
    except Exception:
        # swallow to avoid breaking delivery flow
        pass


def delivery_list(request):
    deliveries_queryset = (
        Delivery.objects.filter(is_archived=False)
        .select_related("order__customer")
        .prefetch_related(
            Prefetch(
                "order__items",
                queryset=OrderItem.objects.select_related("product_variant__product"),
            )
        )
        .order_by("-delivered_at")
    )

    deliveries_data = []
    for delivery in deliveries_queryset:
        order_data = None
        if delivery.order:
            items_data = []
            for item in delivery.order.items.all():
                items_data.append(
                    {
                        "id": item.id,
                        "quantity": item.quantity,
                        "price_at_order": float(item.price_at_order),
                        "item_total": float(item.item_total),
                        "product_variant": (
                            {
                                "id": item.product_variant.id,
                                "size": item.product_variant.size,
                                "color": item.product_variant.color,
                                "product": (
                                    {
                                        "id": item.product_variant.product.id,
                                        "name": item.product_variant.product.name,
                                    }
                                    if item.product_variant.product
                                    else None
                                ),
                            }
                            if item.product_variant
                            else None
                        ),
                    }
                )
            order_data = {
                "id": delivery.order.id,
                "order_id": delivery.order.order_id,
                "customer": (
                    {
                        "id": delivery.order.customer.id,
                        "username": delivery.order.customer.username,
                    }
                    if delivery.order.customer
                    else None
                ),
                "total_cost": float(delivery.order.get_total_cost),
                "items": items_data,
            }

        deliveries_data.append(
            {
                "id": delivery.id,
                "delivery_status": delivery.delivery_status,
                "delivered_at": (
                    delivery.delivered_at.isoformat() if delivery.delivered_at else None
                ),
                "proof_of_delivery_image": (
                    delivery.proof_of_delivery_image.url
                    if delivery.proof_of_delivery_image
                    else None
                ),
                "delivery_note": delivery.delivery_note or "",
                "order": order_data,
            }
        )

    deliveries_json = json.dumps(deliveries_data)
    all_orders = Order.objects.all().select_related("customer")

    return render(
        request,
        "delivery/delivery_list.html",
        {
            "deliveries": deliveries_queryset,
            "all_orders": all_orders,
            "deliveries_json": deliveries_json,
        },
    )


login_required
def delivery_detail(request, delivery_id):
    """
    Display detailed information about a specific delivery
    """
    # keep select_related safe and simple
    delivery_qs = Delivery.objects.select_related(
        "order", "order__customer"
    ).prefetch_related("order__items__product_variant__product")

    delivery = get_object_or_404(delivery_qs, pk=delivery_id)
    order = delivery.order

    def format_address_from_order(o):
        # 1) If there's a single text field
        if hasattr(o, "shipping_address") and o.shipping_address:
            val = getattr(o, "shipping_address")
            if isinstance(val, str):
                return val.strip()

        # 2) Common split-field patterns
        field_groups = [
            (
                "shipping_address_line1",
                "shipping_address_line2",
                "shipping_city",
                "shipping_province",
                "shipping_postal_code",
                "shipping_country",
            ),
            (
                "address_line1",
                "address_line2",
                "city",
                "province",
                "postal_code",
                "country",
            ),
            ("street", "city", "province", "postal_code", "country"),
        ]
        for group in field_groups:
            parts = []
            for fname in group:
                if hasattr(o, fname):
                    fval = getattr(o, fname)
                    if fval:
                        parts.append(str(fval).strip())
            if parts:
                return ", ".join(parts)

        # 3) If shipping_address is a related object with address parts
        try:
            sa = getattr(o, "shipping_address", None)
            if sa and not isinstance(sa, str):
                parts = []
                for fname in (
                    "address_line1",
                    "address_line2",
                    "street",
                    "city",
                    "province",
                    "postal_code",
                    "country",
                ):
                    if hasattr(sa, fname):
                        v = getattr(sa, fname)
                        if v:
                            parts.append(str(v).strip())
                if parts:
                    return ", ".join(parts)
        except Exception:
            pass

        return ""

    shipping_address_display = format_address_from_order(order) or "—"

    context = {
        "delivery": delivery,
        "shipping_address_display": shipping_address_display,
    }

    return render(request, "delivery/delivery_detail.html", context)


def archive_list(request):
    deliveries = (
        Delivery.objects.filter(is_archived=True)
        .select_related("order", "order__customer")
        .prefetch_related("order__items__product_variant__product")
    )
    return render(request, "delivery/archive_list.html", {"deliveries": deliveries})


@csrf_exempt
@require_POST
def archive_deliveries(request):
    data = json.loads(request.body)
    ids = data.get("ids", [])
    
    # --- ADDED AUDIT LOGGING ---
    count = Delivery.objects.filter(id__in=ids, is_archived=False).update(is_archived=True)
    if count > 0:
        try:
            log_audit(
                user=request.user if request.user.is_authenticated else None,
                action="update",
                instance=None, # Bulk action, no single instance
                changes={"deliveries_archived": ids},
                request=request,
            )
        except Exception:
            pass
    # --- END ADDED AUDIT LOGGING ---
    
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def restore_deliveries(request):
    data = json.loads(request.body)
    ids = data.get("ids", [])
    
    # --- ADDED AUDIT LOGGING ---
    count = Delivery.objects.filter(id__in=ids, is_archived=True).update(is_archived=False)
    if count > 0:
        try:
            log_audit(
                user=request.user if request.user.is_authenticated else None,
                action="update",
                instance=None, # Bulk action
                changes={"deliveries_restored": ids},
                request=request,
            )
        except Exception:
            pass
    # --- END ADDED AUDIT LOGGING ---
    
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def permanently_delete_deliveries(request):
    data = json.loads(request.body)
    ids = data.get("ids", [])
    
    # --- ADDED AUDIT LOGGING ---
    # Retrieve delivery IDs before deleting them
    deliveries_to_delete = Delivery.objects.filter(id__in=ids)
    deleted_ids = list(deliveries_to_delete.values_list('id', flat=True))
    
    count, _ = deliveries_to_delete.delete()
    
    if count > 0:
        try:
            log_audit(
                user=request.user if request.user.is_authenticated else None,
                action="delete",
                instance=None, # Bulk action
                changes={"deliveries_permanently_deleted": deleted_ids},
                request=request,
            )
        except Exception:
            pass
    # --- END ADDED AUDIT LOGGING ---
    
    return JsonResponse({"success": True})


# @staff_member_required
@require_POST
def add_delivery(request):
    order_id = request.POST.get("order")
    delivery_status = request.POST.get("delivery_status")

    if order_id and delivery_status:
        try:
            order = get_object_or_404(Order, pk=order_id)
            
            if Delivery.objects.filter(order=order).exists():
                messages.warning(
                    request, f"Delivery already exists for Order {order.order_id}."
                )
            else:
                delivery = Delivery.objects.create(order=order, delivery_status=delivery_status)
                messages.success(
                    request, f"Delivery created for Order {order.order_id}."
                )
                
                # AUDIT LOGGING IS CORRECTLY HERE
                try:
                    log_audit(
                        user=request.user if request.user.is_authenticated else None,
                        action="create",
                        instance=delivery, # Log the created instance
                        changes={"delivery_created_for_order": order.order_id},
                        request=request,
                    )
                except Exception:
                    pass
            return redirect("delivery:delivery_list")
        except Order.DoesNotExist:
            messages.error(request, "Invalid Order selected.")
    else:
        messages.error(request, "Please select an Order and Delivery Status.")

    return redirect("delivery:delivery_list")


# @staff_member_required
def confirm_delivery(request, delivery_id):
    delivery = get_object_or_404(Delivery, pk=delivery_id)
    previous_status = delivery.delivery_status # Capture for audit

    # If already delivered, notify and return
    if previous_status == Delivery.DELIVERED:
        messages.info(
            request,
            f"Delivery for Order {delivery.order.order_id} was already confirmed.",
        )
        return redirect("delivery:delivery_list")

    # Require proof before confirming
    if not delivery.proof_of_delivery_image:
        messages.warning(
            request,
            "Photo proof is required before confirming delivery. Please upload proof.",
        )
        # Redirect user to the proof upload form (GET shows the form)
        return redirect(reverse("delivery:complete_delivery", args=[delivery.id]))

    # If proof exists, proceed to mark delivered
    delivery.delivery_status = Delivery.DELIVERED
    if not delivery.delivered_at:
        delivery.delivered_at = timezone.now()
    delivery.save()
    
    # AUDIT LOGGING IS CORRECTLY HERE
    try:
        log_audit(
            user=request.user if request.user.is_authenticated else None,
            action="update",
            instance=delivery,
            changes={"delivery_status": [previous_status, Delivery.DELIVERED]},
            request=request,
        )
    except Exception:
        pass
        
    messages.success(
        request, f"Delivery for Order {delivery.order.order_id} has been confirmed."
    )
    return redirect("delivery:delivery_list")


@login_required
@require_POST
@csrf_exempt
def upload_proof(request, delivery_id):
    """
    Accepts a file upload and optional 'mark_delivered'.
    Stores the image on the Delivery and optionally marks it Delivered.
    """
    delivery = get_object_or_404(Delivery, pk=delivery_id)
    previous_status = delivery.delivery_status # Capture for audit

    uploaded_file = request.FILES.get("proof_of_delivery_image") or request.FILES.get(
        "proof"
    )
    
    if not uploaded_file:
        if request.content_type == "application/json" or request.is_ajax():
            return JsonResponse(
                {
                    "success": False,
                    "error": "No file provided as proof_of_delivery_image.",
                },
                status=400,
            )
        messages.error(request, "No proof image uploaded.")
        return redirect(
            request.META.get("HTTP_REFERER", reverse("delivery:delivery_list"))
        )

    # Save the uploaded image and note
    delivery.proof_of_delivery_image = uploaded_file
    delivery.delivery_note = request.POST.get("delivery_note", delivery.delivery_note)
    
    # --- ADDED AUDIT LOGGING (for Proof Upload) ---
    # Save the instance within a transaction to capture initial changes before optional status change
    try:
        with transaction.atomic():
            delivery.save()
            log_audit(
                user=request.user if request.user.is_authenticated else None,
                action="update",
                instance=delivery,
                changes={"proof_uploaded": True},
                request=request,
            )
    except Exception:
        pass
    # --- END ADDED AUDIT LOGGING ---

    # Optionally mark delivered if requested and photo now exists
    mark_delivered = request.POST.get("mark_delivered") in ["1", "true", "True", "on"]
    if mark_delivered:
        delivery.delivery_status = Delivery.DELIVERED
        delivery.delivered_at = delivery.delivered_at or timezone.now()
        
        # --- ADDED AUDIT LOGGING (for Status Change from API) ---
        if previous_status != Delivery.DELIVERED:
            delivery.save() # Saves the status and delivered_at changes
            try:
                log_audit(
                    user=request.user if request.user.is_authenticated else None,
                    action="update",
                    instance=delivery,
                    changes={"delivery_status": [previous_status, Delivery.DELIVERED]},
                    request=request,
                )
            except Exception:
                pass
        # --- END ADDED AUDIT LOGGING ---

    if request.content_type == "application/json" or request.is_ajax():
        return JsonResponse({"success": True, "message": "Proof uploaded."})
        
    messages.success(
        request,
        "Proof uploaded successfully."
        + (" Delivery marked as Delivered." if mark_delivered and previous_status != Delivery.DELIVERED else ""),
    )
    return redirect(request.META.get("HTTP_REFERER", reverse("delivery:delivery_list")))


@login_required
@require_POST
@csrf_exempt
def update_delivery_status_view(request, delivery_id):
    """
    Simple JSON endpoint to update status.
    POST JSON: {"status": "<new_status>"}
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse(
            {"success": False, "error": "Invalid JSON body."}, status=400
        )

    new_status = data.get("status")
    if not new_status:
        return JsonResponse(
            {"success": False, "error": 'Missing "status" field.'}, status=400
        )

    delivery = get_object_or_404(Delivery, pk=delivery_id)

    valid_statuses = [choice[0] for choice in Delivery.DELIVERY_STATUS_CHOICES]
    if new_status not in valid_statuses:
        return JsonResponse(
            {"success": False, "error": f"Invalid status: {new_status}"}, status=400
        )

    # Enforce: cannot set to DELIVERED unless proof exists
    if new_status == Delivery.DELIVERED and not delivery.proof_of_delivery_image:
        return JsonResponse(
            {
                "success": False,
                "error": "Photo proof is required to mark as Delivered. Upload proof via the upload_proof endpoint first.",
            },
            status=400,
        )

    # remember previous status for auditing
    previous = delivery.delivery_status

    delivery.delivery_status = new_status
    if new_status == Delivery.DELIVERED:
        delivery.delivered_at = delivery.delivered_at or timezone.now()
    delivery.save()
    messages.success(
                        request,
                        f'delivery status updated successfully.',
                    )

    # AUDIT LOGGING IS CORRECTLY HERE
    if previous != new_status:
        try:
            log_audit(
                user=request.user if request.user.is_authenticated else None,
                action="update",
                instance=delivery,
                changes={"delivery_status": [previous, new_status]},
                request=request,
            )
        except Exception:
            pass

    try:
        send_delivery_status_update_email(delivery)
    except Exception:
        pass

    return JsonResponse({"success": True, "message": "Status updated."})


@login_required
def confirm_delivery(request, delivery_id):
    """
    Staff action: will not mark as Delivered if no photo exists.
    If photo missing, redirects to upload_proof page.
    """
    delivery = get_object_or_404(Delivery, pk=delivery_id)
    previous_status = delivery.delivery_status # Capture for audit

    if delivery.delivery_status == Delivery.DELIVERED:
        messages.info(request, "Delivery already marked as Delivered.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    if not delivery.proof_of_delivery_image:
        messages.warning(
            request,
            "Photo proof required before confirming delivery. Please upload proof.",
        )
        # Note: You have two functions named confirm_delivery, which is a bug. 
        # I'll assume you meant to redirect to the correct upload_proof view, 
        # which is one of the views defined further down.
        return redirect(reverse("delivery:upload_proof", kwargs={'delivery_id': delivery.id})) 

    delivery.delivery_status = Delivery.DELIVERED
    delivery.delivered_at = delivery.delivered_at or timezone.now()
    delivery.save()
    
    # --- ADDED AUDIT LOGGING (for Staff Confirmation) ---
    if previous_status != Delivery.DELIVERED:
        try:
            log_audit(
                user=request.user if request.user.is_authenticated else None,
                action="update",
                instance=delivery,
                changes={"delivery_status": [previous_status, Delivery.DELIVERED]},
                request=request,
            )
        except Exception:
            pass
    # --- END ADDED AUDIT LOGGING ---
    
    try:
        send_delivery_status_update_email(delivery)
    except Exception:
        pass

    messages.success(request, "Delivery confirmed and marked as Delivered.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


def complete_delivery_view(request, delivery_id):
    delivery = get_object_or_404(Delivery, pk=delivery_id)
    previous_status = delivery.delivery_status # Capture for audit

    if request.method == "POST":
        form = ProofOfDeliveryForm(request.POST, request.FILES, instance=delivery)

        if form.is_valid():
            # 1. Save the delivery (updates status, saves image/note)
            form.save()
            new_status = delivery.delivery_status # Capture new status

            # 2. Check the final status and update delivered_at via logic/signal
            if delivery.delivery_status == Delivery.DELIVERED:
                if not delivery.delivered_at:
                    delivery.delivered_at = timezone.now()
                    delivery.save(update_fields=["delivered_at"])

                messages.success(
                    request,
                    f"Proof of Delivery uploaded and status set to DELIVERED for Order {delivery.order.order_id}.",
                )
            # FAILED status is also a key action
            elif delivery.delivery_status == Delivery.FAILED: 
                 messages.warning(
                    request,
                    f"Proof/Note recorded. Status set to FAILED for Order {delivery.order.order_id}.",
                )
            
            # --- ADDED AUDIT LOGGING (for Form Submission) ---
            if previous_status != new_status:
                try:
                    log_audit(
                        user=request.user if request.user.is_authenticated else None,
                        action="update",
                        instance=delivery,
                        changes={"delivery_status": [previous_status, new_status]},
                        request=request,
                    )
                except Exception:
                    pass
            # --- END ADDED AUDIT LOGGING ---

            # 3. Send email notification
            send_delivery_status_update_email(delivery)

            return redirect("delivery:delivery_list")
        else:
            # Add form errors to messages for user feedback
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Error in {field}: {error}")

    else:
        # Pre-populate form with current instance data
        form = ProofOfDeliveryForm(instance=delivery)

    context = {
        "form": form,
        "delivery": delivery,
    }

    return render(request, "delivery/delivery_proof_form.html", context)


# Note: You have two functions named upload_proof and two named confirm_delivery.
# I've left them separated here but highly recommend consolidating to avoid confusion.
@login_required
def upload_proof(request, delivery_id):
    """
    GET: render a simple upload form (ProofOfDeliveryForm).
    POST: validate form, save image/note and optionally mark Delivered (form enforces photo if Delivered).
    """
    delivery = get_object_or_404(Delivery, pk=delivery_id)
    previous_status = delivery.delivery_status # Capture for audit

    if request.method == "GET":
        form = ProofOfDeliveryForm(instance=delivery)
        return render(
            request, "delivery/upload_proof.html", {"form": form, "delivery": delivery}
        )

    # POST handling
    form = ProofOfDeliveryForm(request.POST, request.FILES, instance=delivery)
    if not form.is_valid():
        return render(
            request, "delivery/upload_proof.html", {"form": form, "delivery": delivery}
        )

    # The form's save will update delivery_status from new_status and handle the image field
    form.save()
    new_status = delivery.delivery_status
    
    # Ensure delivered_at is set when status is delivered
    if delivery.delivery_status == Delivery.DELIVERED and not delivery.delivered_at:
        delivery.delivered_at = timezone.now()
        delivery.save()

    # --- ADDED AUDIT LOGGING (for final Form Submission) ---
    if previous_status != new_status or delivery.proof_of_delivery_image:
        changes = {}
        if previous_status != new_status:
            changes['delivery_status'] = [previous_status, new_status]
        if not previous_status and delivery.proof_of_delivery_image:
            changes['proof_uploaded'] = True

        if changes:
             try:
                log_audit(
                    user=request.user if request.user.is_authenticated else None,
                    action="update",
                    instance=delivery,
                    changes=changes,
                    request=request,
                )
             except Exception:
                 pass
    # --- END ADDED AUDIT LOGGING ---
    
    messages.success(
        request,
        "Proof uploaded successfully."
        + (
            " Delivery marked as Delivered."
            if delivery.delivery_status == Delivery.DELIVERED
            else ""
        ),
    )
    return redirect(request.META.get("HTTP_REFERER", reverse("delivery:delivery_list")))