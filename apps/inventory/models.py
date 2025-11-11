# ==============================================================================
# REFACTORED: apps/inventory/models.py
# ==============================================================================

from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from apps.users.models import SupplierProfile
import uuid


# ==============================================================================
# CATEGORY MODEL
# ==============================================================================


class CategoryQuerySet(models.QuerySet):
    """Custom queryset for efficient category filtering."""

    def active(self):
        """Return only active categories."""
        return self.filter(is_active=True)

    def roots(self):
        """Return only root/parent categories."""
        return self.filter(parent__isnull=True)


class CategoryManager(models.Manager):
    """Custom manager for Category model."""

    def get_queryset(self):
        return CategoryQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def active_roots(self):
        """Get active root categories with children prefetched for efficiency."""
        return self.get_queryset().active().roots().prefetch_related("children")


class Category(models.Model):
    """
    Hierarchical category model supporting unlimited nesting depth.
    Uses self-referencing foreign key for parent-child relationships.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    description = models.TextField(blank=True, null=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )

    image =  models.ImageField(upload_to="product_images/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CategoryManager()

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["parent", "is_active"]),
        ]

    def save(self, *args, **kwargs):
        """Auto-generate unique slug if not provided."""
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        """Generate a unique slug from the category name."""
        base_slug = slugify(self.name)
        slug = base_slug
        counter = 1

        while Category.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        return slug

    def __str__(self):
        """Display full hierarchical path."""
        return self.get_full_path()

    # -------------------------------------------------------------------------
    # HIERARCHY METHODS (RECURSIVE)
    # -------------------------------------------------------------------------

    def get_full_path(self, separator=" → "):
        """
        Return full category path from root to current.
        Example: 'Groceries → Beverages → Juice'
        """
        path = []
        current = self

        while current:
            path.insert(0, current.name)
            current = current.parent

        return separator.join(path)

    def get_descendant_ids(self):
        """
        RECURSIVE: Collect IDs of this category and ALL active descendants.

        Returns:
            set: Set of category IDs including self and all children/grandchildren
        """
        category_ids = {self.id}

        # Recursively collect from all active children
        for child in self.children.filter(is_active=True):
            category_ids.update(child.get_descendant_ids())

        return category_ids

    def is_ancestor_of_slug(self, slug):
        """
        RECURSIVE: Check if a category with given slug exists in descendant tree.
        Used for sidebar expansion logic.

        Args:
            slug (str): Category slug to search for

        Returns:
            bool: True if slug found in any descendant
        """
        # Check immediate children
        if self.children.filter(slug=slug, is_active=True).exists():
            return True

        # Recursively check deeper levels
        for child in self.children.filter(is_active=True):
            if child.is_ancestor_of_slug(slug):
                return True

        return False

    def get_all_products_count(self):
        """
        Get count of active products in this category + ALL descendants.
        Uses recursive get_descendant_ids() method.

        Returns:
            int: Total product count
        """
        category_ids = self.get_descendant_ids()

        return Product.objects.filter(
            category_id__in=category_ids, is_active=True, is_deleted=False
        ).count()

    def get_direct_products_count(self):
        """
        Get count of products directly assigned to this category only.
        Does NOT include descendant products.

        Returns:
            int: Direct product count
        """
        return self.inventory_products.filter(is_active=True, is_deleted=False).count()
    def get_direct_children_count(self):
        """
        Get count of direct child categories (subcategories).
        Does NOT include descendants of descendants.
        
        Returns:
            int: Direct children count
        """
        return self.children.filter(is_active=True).count()

    def has_children(self):
        """
        Check if category has any active child categories.
        
        Returns:
            bool: True if has children, False otherwise
        """
        return self.children.filter(is_active=True).exists()
    def get_root(self):
        """
        RECURSIVE: Traverse up the tree to find the root/top-level parent category.
        
        Returns:
            Category: The root parent category (or self if already root)
        
        Example:
            >>> sugars = Category.objects.get(name="Sugars and Sweeteners")
            >>> sugars.get_root().name
            'Groceries'
        """
        current = self
        while current.parent is not None:
            current = current.parent
        return current

# ==============================================================================
# PRODUCT MODEL
# ==============================================================================


class ProductQuerySet(models.QuerySet):
    """Custom queryset for Product with common filters."""

    def active(self):
        """Return only active, non-deleted products."""
        return self.filter(is_active=True, is_deleted=False)

    def in_stock(self):
        """Return products with stock > 0."""
        return self.filter(stock_quantity__gt=0)

    def low_stock(self):
        """Return products at or below reorder level."""
        return self.filter(stock_quantity__lte=models.F("reorder_level"))


class ProductManager(models.Manager):
    """Custom manager for Product model."""

    def get_queryset(self):
        return ProductQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()


class Product(models.Model):
    """Product model with soft delete and inventory tracking."""

    # Identification
    product_id = models.CharField(max_length=10, unique=True, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)

    # Media & Description
    image = models.ImageField(upload_to="product_images/", blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    # Relationships
    supplier_profile = models.ForeignKey(
        SupplierProfile, on_delete=models.CASCADE, null=True, blank=True
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inventory_products",
    )

    # Pricing
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    last_purchase_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Inventory
    stock_quantity = models.PositiveIntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=0)
    # unit = models.CharField(max_length=50, default="unit")
    unit = models.CharField(max_length=50,)

    # Analytics
    total_sales = models.PositiveIntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    # Status
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = ProductManager()

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["category", "is_active", "is_deleted"]),
        ]

    def save(self, *args, **kwargs):
        """Auto-generate product_id and slug if not set."""
        if not self.pk and not self.product_id:
            self.product_id = uuid.uuid4().hex[:10].upper()

        if not self.slug:
            self.slug = self._generate_unique_slug()

        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        """Generate unique slug from product name."""
        base_slug = slugify(self.name)
        slug = base_slug
        counter = 1

        while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        return slug

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        """Soft delete: mark as deleted instead of removing from database."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        """Restore a soft-deleted product."""
        self.is_deleted = False
        self.deleted_at = None
        self.save()

    # inside Product model


    def get_forecasted_reorder_level(self, safety_factor=1.5, min_reorder_level=5):
        """Estimate reorder level from past sales or default."""
        forecast_qty = self.stock_quantity  # replace with real forecast logic
        if forecast_qty is None:
            forecast_qty = min_reorder_level
        new_reorder_level = max(int(forecast_qty * safety_factor), min_reorder_level)
        return new_reorder_level, forecast_qty, None

    def update_dynamic_reorder_level(self, safety_factor=1.5, min_reorder_level=5):
        """Update reorder level dynamically."""
        try:
            new_level, forecast_qty, error = self.get_forecasted_reorder_level(
                safety_factor=safety_factor,
                min_reorder_level=min_reorder_level,
            )
            self.reorder_level = new_level
            self.save(update_fields=["reorder_level"])
            return True, new_level, forecast_qty, error
        except Exception as e:
            return False, None, None, str(e)



# ==============================================================================
# OTHER MODELS
# ==============================================================================


class StockMovement(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    movement_type = models.CharField(
        max_length=10, choices=[("IN", "Stock In"), ("OUT", "Stock Out")]
    )
    quantity = models.PositiveIntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.movement_type} - {self.product.name} ({self.quantity})"


class DemandCheckLog(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    forecasted_quantity = models.IntegerField()
    current_stock = models.IntegerField()
    restock_needed = models.BooleanField()
    checked_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-checked_at"]

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save()

    def __str__(self):
        return f"{self.product.name} - {self.checked_at.strftime('%Y-%m-%d')}"


class RestockLog(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    forecasted_quantity = models.IntegerField()
    current_stock = models.IntegerField()
    checked_at = models.DateTimeField(auto_now_add=True)
    is_handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-checked_at"]

    def __str__(self):
        return f"{self.product.name} - {'Handled' if self.is_handled else 'Pending'}"
