from django.contrib import admin
from .models import Product, Category, StockMovement, DemandCheckLog, RestockLog


# --- Inline for StockMovement under Product ---
class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    readonly_fields = ("timestamp",)
    can_delete = False
    verbose_name_plural = "Stock Movements"
    show_change_link = True


# --- Product Admin ---
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "product_id",
        "category",
        "stock_quantity",
        "price",
        "is_deleted",
        "supplier_profile",
        "created_at",
    )
    list_filter = ("category", "is_deleted", "supplier_profile", "created_at")
    search_fields = ("name", "product_id", "description")
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "product_id",
                    "name",
                    "slug",
                    "description",
                    "image",
                    "category",
                    "supplier_profile",
                    "unit",
                )
            },
        ),
        ("Pricing", {"fields": ("price", "cost_price", "last_purchase_price")}),
        ("Inventory", {"fields": ("stock_quantity",)}),
        ("Sales Data", {"fields": ("total_sales", "total_revenue")}),
        (
            "Status & Timestamps",
            {
                "fields": (
                    "is_active",
                    "is_deleted",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    readonly_fields = ("product_id", "created_at", "updated_at", "deleted_at")

    inlines = [StockMovementInline]


# --- Stock Movement Admin ---
@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "movement_type", "quantity", "timestamp")
    list_filter = ("movement_type", "timestamp")
    search_fields = ("product__name",)
    readonly_fields = ("timestamp",)


# --- Demand Check Log Admin ---
@admin.register(DemandCheckLog)
class DemandCheckLogAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "forecasted_quantity",
        "current_stock",
        "restock_needed",
        "checked_at",
        "is_deleted",
    )
    list_filter = ("restock_needed", "is_deleted", "checked_at")
    search_fields = ("product__name",)
    readonly_fields = ("checked_at", "deleted_at")


# --- Restock Log Admin ---
@admin.register(RestockLog)
class RestockLogAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "forecasted_quantity",
        "current_stock",
        "is_handled",
        "checked_at",
    )
    list_filter = ("is_handled", "checked_at")
    search_fields = ("product__name",)
    readonly_fields = ("checked_at",)


# --- Category Admin ---
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "parent", "is_active", "created_at")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "slug", "description", "parent", "image", "is_active", "created_at", "updated_at")
