from django.contrib import admin
from .models import Delivery


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("order", "delivery_status", "delivered_at")
    list_filter = ("delivery_status",)
    search_fields = ("order__order_id",)
    readonly_fields = ("delivered_at",)


#     from django.contrib import admin
# from django.utils.html import format_html # For creating HTML links
# from django.urls import reverse # For creating URLs

# from .models import Delivery # Your Delivery model
# # Assuming you want to link back to the Order in its admin view
# from apps.orders.models import Order # Assuming Order is in apps.orders.models

# @admin.register(Delivery)
# class DeliveryAdmin(admin.ModelAdmin):
#     list_display = (
#         'delivery_id',       # Custom delivery ID
#         'order_link',        # Custom method to display order and link
#         'customer_username', # Custom method to display customer username
#         'delivery_status',
#         'delivered_at',
#         'is_archived',       # Show archived status
#     )
#     list_filter = (
#         'delivery_status',
#         'is_archived',       # Filter by archived status
#         ('delivered_at', admin.DateFieldListFilter), # Filter by delivered date
#     )
#     search_fields = (
#         'delivery_id',
#         'order__order_id',
#         'order__customer__username', # Search by customer username
#         'order__customer__email',    # Search by customer email
#     )
#     readonly_fields = ('delivered_at', 'delivery_id',) # delivery_id should also be read-only

#     # Allow direct editing of delivery status and archived status from the list view
#     list_editable = ('delivery_status', 'is_archived',)

#     # Ordering of the list
#     ordering = ('-delivered_at', '-id',) # Show most recent deliveries first

#     # Optional: Customize the detail page form layout
#     fieldsets = (
#         (None, {
#             'fields': ('delivery_id', 'order',)
#         }),
#         ('Delivery Details', {
#             'fields': ('delivery_status', 'delivered_at',)
#         }),
#         ('Archiving', {
#             'fields': ('is_archived',)
#         }),
#     )

#     # Custom methods for list_display
#     @admin.display(description='Order ID')
#     def order_link(self, obj):
#         if obj.order:
#             # Create a link to the Order's admin change page
#             # Make sure 'admin:app_label_model_name_change' is correct for your Order model
#             # e.g., 'admin:orders_order_change' if Order is in 'orders' app
#             link = reverse("admin:orders_order_change", args=[obj.order.id])
#             return format_html('<a href="{}">{}</a>', link, obj.order.order_id)
#         return "N/A"
#     order_link.allow_tags = True # Required for format_html

#     @admin.display(description='Customer')
#     def customer_username(self, obj):
#         if obj.order and obj.order.customer:
#             return obj.order.customer.username
#         return "—" # Display a dash if no customer
