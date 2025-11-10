from django.contrib import admin
from django.contrib.auth import get_user_model
from .models import CustomerProfile, SupplierProfile, EmailVerification

User = get_user_model()


# --------------------
# Inline Profiles
# --------------------
class CustomerProfileInline(admin.StackedInline):
    model = CustomerProfile
    can_delete = False
    verbose_name_plural = "Customer Profile"
    fields = ("phone", "street_address", "city", "province", "zip_code")  # updated


class SupplierProfileInline(admin.StackedInline):
    model = SupplierProfile
    can_delete = False
    verbose_name_plural = "Supplier Profile"
    fields = ("phone", "address", "company_name", "business_registration")


# --------------------
# User Admin
# --------------------
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_superuser",
        "role",
        "is_approved",
    )
    list_filter = ("role", "is_staff", "is_superuser", "is_active", "is_approved")
    search_fields = ("username", "email", "first_name", "last_name", "role")
    ordering = ("username",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Important dates",
            {"fields": ("last_login", "date_joined", "date_requested")},
        ),
        ("Custom Fields", {"fields": ("role", "is_approved")}),
    )

    readonly_fields = ("date_joined", "last_login", "date_requested")

    inlines = [CustomerProfileInline, SupplierProfileInline]

    def get_inline_instances(self, request, obj=None):
        """Show appropriate inline based on user role"""
        if obj and obj.role == "customer":
            return [CustomerProfileInline(self.model, self.admin_site)]
        elif obj and obj.role == "supplier":
            return [SupplierProfileInline(self.model, self.admin_site)]
        return []


# --------------------
# CustomerProfile Admin
# --------------------
@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "street_address", "city", "province", "zip_code")
    search_fields = ("user__username", "user__email", "phone")
    list_filter = ("user__role",)


# --------------------
# SupplierProfile Admin
# --------------------
@admin.register(SupplierProfile)
class SupplierProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "company_name", "phone", "business_registration")
    search_fields = ("user__username", "company_name", "business_registration")
    list_filter = ("user__role",)


# --------------------
# EmailVerification Admin
# --------------------
@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "code", "created_at", "is_expired")
    search_fields = ("user__username", "user__email")
    list_filter = ("created_at",)
    readonly_fields = ("created_at",)
