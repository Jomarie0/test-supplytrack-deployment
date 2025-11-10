from django.contrib import admin
from django.shortcuts import render
from django.urls import path
from django.utils import timezone
from django.utils.safestring import mark_safe

# Assuming you have imported your models correctly
from .models import Order, OrderItem, ManualOrder, ManualOrderItem


# --- OrderItem Inline Admin ---
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ["product_variant", "quantity", "price_at_order"]
    readonly_fields = ["price_at_order"]


# --- Order Admin ---
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = [OrderItemInline]

    # =========================================================
    # 1. CUSTOM METHODS (MUST BE DEFINED BEFORE fieldsets/readonly_fields)
    # =========================================================

    @admin.display(description="GCash Proof")
    def gcash_image_preview(self, obj):
        """Displays the GCash reference image if payment method is GCASH."""
        if obj.payment_method == "GCASH" and obj.gcash_reference_image:
            # The mark_safe function is critical to render HTML instead of plain text
            return mark_safe(
                f'<a href="{obj.gcash_reference_image.url}" target="_blank">'
                f'<img src="{obj.gcash_reference_image.url}" style="max-height: 200px; max-width: 200px; border: 1px solid #ccc;"/>'
                f"</a><br>Click to view full image"
            )
        elif obj.payment_method == "GCASH" and not obj.gcash_reference_image:
            return mark_safe(
                '<span style="color: red; font-weight: bold;">⚠️ GCash selected but NO image uploaded.</span>'
            )
        return "N/A"  # For COD or other methods

    @admin.display(description="Customer")
    def customer_display(self, obj):
        return obj.customer.username if obj.customer else "Guest"

    @admin.display(description="Total Price")
    def get_total_cost_display(self, obj):
        # Using a distinct method name for clarity in admin display
        try:
            return f"₱{obj.get_total_cost:.2f}"
        except AttributeError:
            total = sum(item.quantity * item.price_at_order for item in obj.items.all())
            return f"₱{total:.2f}"
    
    # NEW: Display method for payment status
    @admin.display(description="Payment Status")
    def payment_status_display(self, obj):
        # Use simple color coding for visibility
        color_map = {
            'paid': 'green',
            'unpaid': 'red',
            'refunded': 'blue',
            'partially_refunded': 'orange',
        }
        color = color_map.get(obj.payment_status, 'black')
        return mark_safe(f'<span style="font-weight: bold; color: {color};">{obj.get_payment_status_display()}</span>')


    @admin.display(boolean=True, description="Deleted?")
    def is_deleted_display(self, obj):
        return obj.is_deleted

    # =========================================================
    # 2. ADMIN CONFIGURATION LISTS (Reference the methods above)
    # =========================================================

    list_display = (
        "order_id",
        "customer_display",
        "get_total_cost_display",
        "payment_method",
        "status",
        "payment_status_display",  # ADDED TO LIST DISPLAY
        "order_date",
        "is_deleted_display",
    )

    list_editable = ("status",)
    list_filter = ("status", "payment_method", "payment_status", "order_date", "is_deleted") # ADDED payment_status
    search_fields = (
        "order_id",
        "customer__username",
        "customer__email",
        "customer__customer_profile__street_address",
    )

    # --- The fieldsets list now correctly references the custom method and adds payment status fields ---
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "order_id", # ADDED order_id as readonly for display
                    "customer",
                    "status",
                    "order_date",
                    "expected_delivery_date",
                )
            },
        ),
        (
            "Payment Information",
            {
                "fields": (
                    "payment_method",
                    "payment_status", # ADDED payment_status
                    "get_total_cost_display", # Total cost moved here
                )
            },
        ),
        # --- GCASH PROOF FIELDSET ---
        (
            "Payment Verification (GCash Proof)",
            {
                "fields": (
                    "gcash_image_preview",
                    "payment_verified_at", # ADDED verification timestamp
                    "payment_verified_by", # ADDED verifier staff user
                ),
                "classes": ("wide", "extrapretty"),
                "description": "Image reference uploaded by the customer for GCash payment. Payment status is automatically updated upon approval/completion.",
            },
        ),
        # --- END GCASH FIELDSET ---
        (
            "Customer Profile Information (Read-Only)",
            {
                "fields": ("get_customer_phone", "get_shipping_address"), # Added helpers for display
                "classes": ("collapse",),
                "description": "Address information is retrieved from the customer's CustomerProfile. No direct address fields are stored in Order model.",
            },
        ),
        (
            "Stock & Deletion Information",
            {
                "fields": ("stock_deducted", "stock_deducted_at", "stock_restored", "stock_restored_at", "is_deleted", "deleted_at"),
                "classes": ("collapse",),
                "description": "Stock and soft deletion management fields.",
            },
        ),
    )

    # --- readonly_fields updated to include new fields and helpers ---
    readonly_fields = (
        "order_id", # Set as read-only
        "gcash_image_preview",
        "order_date",
        "deleted_at",
        "get_total_cost_display",
        "payment_status", # Payment status is auto-managed, so keep it read-only
        "payment_verified_at",
        "payment_verified_by",
        "get_customer_phone", # Read-only helper method
        "get_shipping_address", # Read-only helper method
    )
    
    # *** Actions and get_urls remain the same for brevity ***

    # Override get_queryset to exclude soft-deleted items by default
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_deleted=False)
    
    # ... (Actions and get_urls methods are omitted here for brevity, they are the same)
    
# ... (ManualOrderItemInline)

class ManualOrderItemInline(admin.TabularInline):
    model = ManualOrderItem
    extra = 1

# --- Manual Order Admin ---
@admin.register(ManualOrder)
class ManualOrderAdmin(admin.ModelAdmin):
    inlines = [ManualOrderItemInline] # Added inlines for completeness

    # =========================================================
    # 1. CUSTOM METHODS
    # =========================================================
    
    # NEW: GCash image preview for ManualOrder
    @admin.display(description="GCash Proof")
    def manual_gcash_image_preview(self, obj):
        """Displays the GCash reference image for Manual Orders."""
        if obj.payment_method == "GCASH" and obj.gcash_reference_image:
            return mark_safe(
                f'<a href="{obj.gcash_reference_image.url}" target="_blank">'
                f'<img src="{obj.gcash_reference_image.url}" style="max-height: 200px; max-width: 200px; border: 1px solid #ccc;"/>'
                f"</a><br>Click to view full image"
            )
        return "N/A"

    @admin.display(description="Total Price")
    def get_total_cost_display(self, obj):
        try:
            return f"₱{obj.get_total_cost:.2f}"
        except AttributeError:
            total = sum(item.quantity * item.price_at_order for item in obj.items.all())
            return f"₱{total:.2f}"

    @admin.display(description="Customer")
    def get_customer_display(self, obj):
        return obj.get_customer_display()

    @admin.display(description="Payment Status")
    def payment_status_display(self, obj):
        color_map = {
            'paid': 'green',
            'unpaid': 'red',
            'refunded': 'blue',
            'partially_refunded': 'orange',
        }
        color = color_map.get(obj.payment_status, 'black')
        return mark_safe(f'<span style="font-weight: bold; color: {color};">{obj.get_payment_status_display()}</span>')


    # =========================================================
    # 2. ADMIN CONFIGURATION LISTS
    # =========================================================

    list_display = (
        "manual_order_id",
        "get_customer_display",
        "get_total_cost_display",
        "payment_method",
        "status",
        "payment_status_display", # ADDED
        "order_date",
        "is_deleted",
        "created_by",
    )
    list_filter = ("status", "payment_method", "payment_status", "order_source", "is_deleted") # ADDED payment_status
    list_editable = ("status",)
    search_fields = (
        "manual_order_id",
        "customer_name",
        "customer_email",
        "customer_phone",
        "shipping_address",
    )

    # --- fieldsets updated for ManualOrder ---
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "manual_order_id",
                    "order_source",
                    "status",
                    "order_date",
                    "expected_delivery_date",
                )
            },
        ),
        (
            "Customer & Contact Information",
            {
                "fields": (
                    "customer",
                    "customer_name",
                    "customer_email",
                    "customer_phone",
                )
            },
        ),
        (
            "Payment Information",
            {
                "fields": (
                    "payment_method",
                    "payment_status", # ADDED payment_status
                    "get_total_cost_display",
                )
            },
        ),
        (
            "Payment Verification (GCash Proof)",
            {
                "fields": (
                    "manual_gcash_image_preview", # NEW custom method
                    "payment_verified_at", # ADDED verification timestamp
                    "payment_verified_by", # ADDED verifier staff user
                    "gcash_reference_image", # Allow upload/view
                ),
                "classes": ("wide", "extrapretty"),
                "description": "For GCash: Staff should verify the payment proof. For COD: Payment status changes to 'paid' when order status is set to 'Completed'.",
            },
        ),
        (
            "Shipping & Notes",
            {
                "fields": ("shipping_address", "billing_address", "notes")
            },
        ),
        (
            "Administration & Deletion",
            {
                "fields": (
                    "created_by",
                    "created_at",
                    "updated_at",
                    "stock_deducted",
                    "stock_deducted_at",
                    "stock_restored",
                    "stock_restored_at",
                    "is_deleted",
                    "deleted_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    # --- readonly_fields updated for ManualOrder ---
    readonly_fields = (
        "manual_order_id",
        "order_date",
        "created_at",
        "updated_at",
        "deleted_at",
        "get_total_cost_display",
        "manual_gcash_image_preview", # NEW custom method
        "payment_status", # Payment status is auto-managed
        "payment_verified_at",
        "payment_verified_by",
    )
    
    # ... (Actions and get_urls methods are omitted here for brevity, they are the same)

# If OrderItem and ManualOrderItem were not registered, you can register them here if needed
# admin.site.register(OrderItem)
# admin.site.register(ManualOrderItem)