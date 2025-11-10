from django.contrib import admin
from .models import ProductVariant, Cart, CartItem, ProductReview


# Admin for ProductVariant model
@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "size", "color", "price", "sku", "is_active")
    list_filter = ("product", "is_active", "size", "color")
    search_fields = ("product__name", "sku", "size", "color")
    # You might want to make 'sku' editable but ensure uniqueness is handled
    # readonly_fields = ('sku',) # If SKU is auto-generated elsewhere
    raw_id_fields = (
        "product",
    )  # Use a raw ID input for product to handle many products


# Inline for CartItems to be displayed within the Cart admin
class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0  # Don't show extra empty forms by default
    raw_id_fields = ("product_variant",)  # Use raw ID for product variant


# Admin for Cart model
@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("user", "session_key", "created_at", "updated_at")
    search_fields = ("user__username", "session_key")
    inlines = [CartItemInline]  # Display cart items directly in cart admin
    readonly_fields = ("created_at", "updated_at")


# Admin for CartItem model (can also be registered standalone if not using inline)
# If you don't use CartItemInline, uncomment this:
# @admin.register(CartItem)
# class CartItemAdmin(admin.ModelAdmin):
#     list_display = ('cart', 'product_variant', 'quantity', 'added_at')
#     list_filter = ('cart', 'product_variant')
#     search_fields = ('cart__user__username', 'product_variant__product__name')


# Admin for ProductReview model
@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "customer", "rating", "is_approved", "created_at")
    list_filter = ("is_approved", "rating", "created_at")
    search_fields = ("product__name", "customer__user__username", "comment")
    actions = ["approve_reviews", "disapprove_reviews"]

    def approve_reviews(self, request, queryset):
        queryset.update(is_approved=True)
        self.message_user(request, "Selected reviews have been approved.")

    approve_reviews.short_description = "Approve selected product reviews"

    def disapprove_reviews(self, request, queryset):
        queryset.update(is_approved=False)
        self.message_user(request, "Selected reviews have been disapproved.")

    disapprove_reviews.short_description = "Disapprove selected product reviews"
