# apps/store/models.py

from django.db import models
from django.conf import settings

# No longer importing Category here as it's now with Product in inventory
from apps.inventory.models import (
    Product,
)  # Product is now imported directly from inventory
from django.utils.text import slugify

# Import CustomerProfile from users app for ProductReview
from apps.users.models import CustomerProfile  # <-- Ensure this import is correct


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants"
    )
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True)
    size = models.CharField(max_length=50, blank=True, null=True)
    color = models.CharField(max_length=50, blank=True, null=True)

    # ADDED: A real database field for price on the ProductVariant
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Price for this specific variant. If blank, defaults to product's base price.",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Designates whether this product variant should be visible in the store.",
    )

    # Modified @property to use the new 'price' field if it exists, otherwise fallback to product's price
    @property
    def get_display_price(
        self,
    ):  # Renamed property to avoid conflict with the new field
        return self.price if self.price is not None else self.product.price

    def save(self, *args, **kwargs):
        if not self.sku:
            size_slug = slugify(self.size) if self.size else "N/A"
            color_slug = slugify(self.color) if self.color else "N/A"
            self.sku = f"{self.product.id}-{size_slug}-{color_slug}"

        # If price is not set, default it to the product's price
        if self.price is None:
            self.price = self.product.price
        super().save(*args, **kwargs)

    def __str__(self):
        variant_str = f"{self.product.name}"
        if self.size:
            variant_str += f" ({self.size}"
            if self.color:
                variant_str += f", {self.color}"
            variant_str += ")"
        elif self.color:
            variant_str += f" ({self.color})"
        return variant_str


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )
    session_key = models.CharField(max_length=40, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.user:
            return f"Cart of {self.user.username}"
        return f"Anonymous Cart {self.session_key or self.id}"

    @property
    def get_cart_total(self):
        return sum(item.item_total for item in self.items.all())

    @property
    def get_cart_item_count(self):
        return self.items.count()

    @property
    def get_cart_total_quantity(self):
        return sum(item.quantity for item in self.items.all())


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product_variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("cart", "product_variant")

    def __str__(self):
        return f"{self.quantity} x {self.product_variant.product.name} ({self.product_variant.sku or 'Default'}) in {self.cart}"

    @property
    def item_total(self):
        # Now use the new 'get_display_price' property from ProductVariant
        return self.quantity * self.product_variant.get_display_price


class ProductReview(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="reviews"
    )
    customer = models.ForeignKey(
        CustomerProfile, on_delete=models.CASCADE, related_name="reviews"
    )
    rating = models.PositiveIntegerField(
        choices=[(i, str(i)) for i in range(1, 6)],
        help_text="Rating from 1 to 5 stars.",
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False, help_text="For moderation.")

    class Meta:
        unique_together = ("product", "customer")
        ordering = ["-created_at"]

    def __str__(self):
        customer_name = (
            self.customer.user.username
            if self.customer and self.customer.user
            else "Unknown Customer"
        )
        return (
            f"Review for {self.product.name} by {customer_name} - {self.rating} stars"
        )
