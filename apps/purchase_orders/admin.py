# apps/purchasing/admin.py

# Standard library
from django.utils import timezone

# Django imports
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Sum, Count, Q

# Local imports
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseOrderNotification


# ------------------------------
# Inline for PurchaseOrder Items
# ------------------------------
class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1
    fields = (
        "product_variant",
        "product_name_text",
        "quantity_ordered",
        "quantity_received",
        "unit_cost",
        "total_price",
    )
    readonly_fields = ("total_price",)
    autocomplete_fields = ["product_variant"]


# ------------------------------
# PurchaseOrder Admin
# ------------------------------
@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    inlines = [PurchaseOrderItemInline]

    list_display = (
        "purchase_order_id",
        "supplier_profile_link",
        "order_date",
        "expected_delivery_date",
        "status_badge",
        "payment_status_badge",
        "payment_method",
        "payment_proof_indicator",  # NEW
        "total_cost",
        "received_date",
        "is_deleted",
    )
    
    list_filter = (
        "status",
        "payment_status",
        "payment_method",
        "supplier_profile",
        "order_date",
        "expected_delivery_date",
        "is_deleted",
        "pay_later",
        ("payment_proof_image", admin.EmptyFieldListFilter),  # NEW - filter by has/no proof
    )
    
    search_fields = (
        "purchase_order_id",
        "supplier_profile__company_name",
        "items__product_variant__product__name",
        "items__product_name_text",
        "notes",
        "refund_reason",
    )
    
    date_hierarchy = "order_date"

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "purchase_order_id",
                    "supplier_profile",
                    "created_by",
                    "order_date",
                    "expected_delivery_date",
                    "status",
                    "notes",
                )
            },
        ),
        (
            "Payment Terms",
            {
                "fields": (
                    "payment_method",
                    "payment_status",
                    "pay_later",
                    "payment_due_date",
                    "total_cost",
                ),
                "description": "Final agreed-upon payment terms and status.",
            },
        ),
        (
            "Payment Proof & Verification",
            {
                "fields": (
                    "payment_proof_image",
                    "payment_proof_preview",  # NEW - shows image preview
                    "payment_verified_at",
                    "payment_verified_by",
                ),
                "classes": ("collapse",),
                "description": "Payment proof upload and verification tracking.",
            },
        ),
        (
            "Refund Information",
            {
                "fields": (
                    "refund_reason",
                    "refund_amount",
                ),
                "classes": ("collapse",),
                "description": "Refund/return request details.",
            },
        ),
        (
            "Receipt Information",
            {
                "fields": ("received_date",),
                "classes": ("collapse",),
                "description": "Fields related to the actual receipt of goods.",
            },
        ),
        (
            "Deletion Information",
            {
                "fields": ("is_deleted", "deleted_at"),
                "classes": ("collapse",),
                "description": "Soft delete management.",
            },
        ),
    )

    readonly_fields = (
        "purchase_order_id", 
        "total_cost", 
        "payment_verified_at", 
        "payment_verified_by",
        "payment_proof_preview",  # NEW
    )
    raw_id_fields = ("created_by",)
    autocomplete_fields = ["supplier_profile"]

    # ------------------------------
    # Custom Display Methods
    # ------------------------------
    @admin.display(description="Supplier")
    def supplier_profile_link(self, obj):
        sp = obj.supplier_profile
        if sp:
            link = reverse(
                f"admin:{sp._meta.app_label}_{sp._meta.model_name}_change", args=[sp.id]
            )
            return format_html(
                '<a href="{}">{}</a>', link, sp.company_name or sp.user.username
            )
        return "N/A"

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            PurchaseOrder.STATUS_DRAFT: "gray",
            PurchaseOrder.STATUS_REQUEST_PENDING: "orange",
            PurchaseOrder.STATUS_SUPPLIER_PRICED: "blue",
            PurchaseOrder.STATUS_CONFIRMED: "green",
            PurchaseOrder.STATUS_IN_TRANSIT: "teal",
            PurchaseOrder.STATUS_PARTIALLY_RECEIVED: "purple",
            PurchaseOrder.STATUS_RECEIVED: "darkgreen",
            PurchaseOrder.STATUS_REFUND: "red",
            PurchaseOrder.STATUS_CANCELLED: "darkgray",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )

    @admin.display(description="Payment Status")
    def payment_status_badge(self, obj):
        colors = {
            "unpaid": "orange",
            "paid": "green",
            "overdue": "red",
            "refunded": "darkred",
            "partially_refunded": "purple",
        }
        color = colors.get(obj.payment_status, "gray")
        
        # Add warning icon for overdue
        icon = ""
        if obj.payment_status == "overdue":
            icon = "⚠️ "
        elif obj.payment_status == "paid":
            icon = "✓ "
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px;">{}{}</span>',
            color,
            icon,
            obj.get_payment_status_display()
        )

    @admin.display(description="Payment Proof")
    def payment_proof_indicator(self, obj):
        """Show if payment proof has been uploaded"""
        if obj.payment_proof_image:
            return format_html(
                '<span style="color: green; font-weight: bold;" title="Payment proof uploaded">✓ Uploaded</span>'
            )
        elif obj.payment_method in ['prepaid', 'net_30'] or obj.pay_later:
            # Payment methods that require proof
            return format_html(
                '<span style="color: orange;" title="Payment proof required">⚠ Required</span>'
            )
        else:
            return format_html(
                '<span style="color: gray;">— N/A</span>'
            )

    @admin.display(description="Payment Proof Preview")
    def payment_proof_preview(self, obj):
        """Show thumbnail preview of payment proof in change form"""
        if obj.payment_proof_image:
            return format_html(
                '<a href="{}" target="_blank">'
                '<img src="{}" style="max-width: 300px; max-height: 300px; border: 1px solid #ddd; padding: 5px;"/>'
                '</a><br/>'
                '<small><a href="{}" target="_blank">View Full Size</a></small>',
                obj.payment_proof_image.url,
                obj.payment_proof_image.url,
                obj.payment_proof_image.url
            )
        return format_html('<span style="color: gray;">No payment proof uploaded</span>')

    # ------------------------------
    # Admin Actions (UPDATED)
    # ------------------------------
    actions = [
        "mark_as_confirmed",
        "mark_as_in_transit",
        "mark_as_partially_received",
        "mark_as_received",
        "mark_as_refund",
        "mark_as_cancelled",
        "mark_payment_as_paid",
        "mark_payment_as_overdue",
        "request_payment_proof",  # NEW
        "soft_delete_pos",
        "restore_pos",
    ]

    @admin.action(description="✅ Mark as Confirmed (Ready for Shipping)")
    def mark_as_confirmed(self, request, queryset):
        updated = queryset.filter(
            status=PurchaseOrder.STATUS_SUPPLIER_PRICED
        ).update(status=PurchaseOrder.STATUS_CONFIRMED)
        self.message_user(
            request, 
            f"{updated} purchase orders marked as Confirmed. "
            f"{queryset.count() - updated} skipped (not in supplier_priced status)."
        )

    @admin.action(description="🚚 Mark as In Transit (OTW)")
    def mark_as_in_transit(self, request, queryset):
        updated = queryset.filter(
            status=PurchaseOrder.STATUS_CONFIRMED
        ).update(status=PurchaseOrder.STATUS_IN_TRANSIT)
        self.message_user(
            request, 
            f"{updated} purchase orders marked as In Transit."
        )

    @admin.action(description="📦 Mark as Partially Received")
    def mark_as_partially_received(self, request, queryset):
        updated = queryset.filter(
            status__in=[PurchaseOrder.STATUS_IN_TRANSIT, PurchaseOrder.STATUS_CONFIRMED]
        ).update(status=PurchaseOrder.STATUS_PARTIALLY_RECEIVED)
        self.message_user(
            request, 
            f"{updated} purchase orders marked as Partially Received."
        )

    @admin.action(description="✔️ Mark as Fully Received")
    def mark_as_received(self, request, queryset):
        count = 0
        for po in queryset:
            if po.status not in [PurchaseOrder.STATUS_RECEIVED, PurchaseOrder.STATUS_CANCELLED]:
                po.status = PurchaseOrder.STATUS_RECEIVED
                po.received_date = timezone.now().date()
                
                # For COD, auto-mark as paid on receipt
                if po.payment_method == 'cod' and po.payment_status != 'paid':
                    po.payment_status = 'paid'
                    po.payment_verified_at = timezone.now()
                    po.payment_verified_by = request.user
                
                po.save()
                count += 1
        self.message_user(
            request, 
            f"{count} purchase orders marked as Received. "
            "(COD orders auto-marked as paid)"
        )

    @admin.action(description="🔄 Mark as Refund Requested")
    def mark_as_refund(self, request, queryset):
        updated = queryset.filter(
            status__in=[PurchaseOrder.STATUS_RECEIVED, PurchaseOrder.STATUS_PARTIALLY_RECEIVED]
        ).update(status=PurchaseOrder.STATUS_REFUND, payment_status='refunded')
        self.message_user(
            request, 
            f"{updated} purchase orders marked as Refund Requested."
        )

    @admin.action(description="❌ Mark as Cancelled")
    def mark_as_cancelled(self, request, queryset):
        count = 0
        for po in queryset:
            if po.status not in [PurchaseOrder.STATUS_RECEIVED, PurchaseOrder.STATUS_REFUND]:
                po.status = PurchaseOrder.STATUS_CANCELLED
                # Reset payment if it was paid
                if po.payment_status == 'paid':
                    po.payment_status = 'unpaid'
                    po.payment_verified_at = None
                    po.payment_verified_by = None
                po.save()
                count += 1
        self.message_user(
            request, 
            f"{count} purchase orders cancelled."
        )

    @admin.action(description="💰 Mark Payment as Paid (with proof verification)")
    def mark_payment_as_paid(self, request, queryset):
        """
        Manually mark payments as paid.
        Note: For prepaid/net30, payment proof should be uploaded first.
        """
        count = 0
        no_proof_count = 0
        
        for po in queryset:
            if po.payment_status != 'paid':
                # Warn if payment proof is missing for methods that require it
                if po.payment_method in ['prepaid', 'net_30'] or po.pay_later:
                    if not po.payment_proof_image:
                        no_proof_count += 1
                        # Still allow manual override, but warn
                
                po.payment_status = 'paid'
                po.payment_verified_at = timezone.now()
                po.payment_verified_by = request.user
                po.save()
                count += 1
        
        message = f"{count} payments marked as Paid."
        if no_proof_count > 0:
            message += f" ⚠️ Warning: {no_proof_count} orders marked paid without payment proof."
        
        self.message_user(request, message)

    @admin.action(description="⚠️ Mark Payment as Overdue")
    def mark_payment_as_overdue(self, request, queryset):
        updated = queryset.filter(
            payment_status='unpaid', 
            pay_later=True
        ).update(payment_status='overdue')
        self.message_user(request, f"{updated} payments marked as Overdue.")

    @admin.action(description="📸 Request Payment Proof (notify staff)")
    def request_payment_proof(self, request, queryset):
        """
        Flag orders that need payment proof.
        In a real implementation, this would send notifications.
        """
        eligible = queryset.filter(
            payment_method__in=['prepaid', 'net_30'],
            payment_proof_image='',
            payment_status='unpaid'
        )
        count = eligible.count()
        
        # In production, you would:
        # 1. Create notification records
        # 2. Send emails to staff/suppliers
        # 3. Update a "proof_requested" flag
        
        self.message_user(
            request, 
            f"{count} orders need payment proof. "
            "(In production, notifications would be sent)"
        )

    @admin.action(description="🗑️ Soft delete selected purchase orders")
    def soft_delete_pos(self, request, queryset):
        count = 0
        for po in queryset:
            po.delete()
            count += 1
        self.message_user(request, f"{count} purchase orders soft-deleted.")

    @admin.action(description="♻️ Restore selected purchase orders")
    def restore_pos(self, request, queryset):
        count = 0
        for po in queryset:
            if hasattr(po, "restore"):
                po.restore()
                count += 1
        self.message_user(request, f"{count} purchase orders restored.")

    # ------------------------------
    # Custom Queryset & Change View
    # ------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related(
            'supplier_profile', 
            'created_by', 
            'payment_verified_by'
        ).prefetch_related('items')

    def save_model(self, request, obj, form, change):
        """
        Auto-set payment_verified_by when admin manually marks as paid.
        Also handle payment proof upload logic.
        """
        if change:
            # If payment status changed to paid
            if 'payment_status' in form.changed_data:
                if obj.payment_status == 'paid' and not obj.payment_verified_by:
                    obj.payment_verified_by = request.user
                    obj.payment_verified_at = timezone.now()
            
            # If payment proof was just uploaded, auto-mark as paid (for prepaid/net30)
            if 'payment_proof_image' in form.changed_data:
                if obj.payment_proof_image:
                    if obj.payment_method in ['prepaid', 'net_30'] or obj.pay_later:
                        if obj.payment_status != 'paid':
                            obj.payment_status = 'paid'
                            obj.payment_verified_at = timezone.now()
                            obj.payment_verified_by = request.user
                            self.message_user(
                                request, 
                                f"Payment proof uploaded and order auto-marked as PAID for {obj.purchase_order_id}",
                                level='SUCCESS'
                            )
        
        super().save_model(request, obj, form, change)


# ------------------------------
# PurchaseOrderNotification Admin
# ------------------------------
@admin.register(PurchaseOrderNotification)
class PurchaseOrderNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "purchase_order",
        "supplier_name",
        "status",
        "message",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "purchase_order__purchase_order_id",
        "supplier_name",
        "message",
    )
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")