# apps/orders/models.py

from django.db import models
from apps.store.models import ProductVariant

import string
import random
from django.contrib.auth import get_user_model
from decimal import Decimal
from django.utils import timezone

User = get_user_model()


def generate_unique_order_id():
    while True:
        order_id = "ORD" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        if not Order.objects.filter(order_id=order_id).exists():
            return order_id


class Order(models.Model):
    """
    Customer Order Model - E-commerce orders placed through the website.

    IMPORTANT ARCHITECTURE NOTE:
    - This model NO LONGER stores shipping/billing addresses directly.
    - All customer address information is retrieved from the related CustomerProfile.
    - This ensures a single source of truth for customer data.
    - For address information, use: order.customer.customer_profile
    """

    order_id = models.CharField(
        max_length=20, unique=True, editable=False, default=generate_unique_order_id
    )

    # Customer reference - REQUIRED for orders (no guest orders)
    customer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="orders",
        help_text="Customer who placed this order. Address info comes from their CustomerProfile.",
    )

    PAYMENT_METHODS = [
        ("COD", "Cash on Delivery"),
        ("GCASH", "GCash Transfer"),
    ]
    payment_method = models.CharField(
        max_length=10, choices=PAYMENT_METHODS, default="COD"
    )

    gcash_reference_image = models.ImageField(
        upload_to="payment_proofs/gcash/customer/",
        null=True,
        blank=True,
        verbose_name="GCash Payment Proof",
    )

    ORDER_STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Processing", "Processing"),
        ("Shipped", "Shipped"),
        ("Completed", "Completed"),
        ("Canceled", "Canceled"),
        ("Returned", "Returned"),
    ]
    status = models.CharField(
        max_length=20, choices=ORDER_STATUS_CHOICES, default="Pending"
    )

    order_date = models.DateTimeField(default=timezone.now)
    expected_delivery_date = models.DateField(null=True, blank=True)

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Stock tracking
    stock_deducted = models.BooleanField(
        default=False, help_text="Indicates if stock has been deducted for this order"
    )
    stock_deducted_at = models.DateTimeField(
        null=True, blank=True, help_text="Timestamp when stock was deducted"
    )

    stock_restored = models.BooleanField(
        default=False,
        help_text="Indicates if stock was restored (e.g., order canceled)",
    )
    stock_restored_at = models.DateTimeField(
        null=True, blank=True, help_text="Timestamp when stock was restored"
    )

    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('paid', 'Paid'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
    ]
    
    payment_status = models.CharField(
        max_length=25,
        choices=PAYMENT_STATUS_CHOICES,
        default='unpaid',
        help_text='Automatically managed based on payment method and order status'
    )
    
    payment_verified_at = models.DateTimeField(
        null=True, 
        blank=True,
        help_text='When payment was verified/confirmed'
    )
    
    payment_verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_payments',
        help_text='Staff who verified/approved the order (for GCash) or marked delivery complete (for COD)'
    )


    class Meta:
        ordering = ["-order_date"]

    # ============================================================
    # ADDRESS HELPER METHODS - REFACTORED
    # These methods retrieve address info from CustomerProfile
    # ============================================================

    def get_customer_name(self):
        """Get customer's full name or username."""
        if not self.customer:
            return "Guest"
        return self.customer.get_full_name() or self.customer.username

    def get_customer_email(self):
        """Get customer's email."""
        if not self.customer:
            return "No email"
        return self.customer.email or "No email"

    def get_customer_phone(self):
        """Retrieve customer phone from profile."""
        if not self.customer:
            return "No phone on file"

        try:
            profile = self.customer.customer_profile
            return profile.phone or "No phone on file"
        except Exception:
            return "No phone on file"

    def get_shipping_address(self):
        """
        Retrieve formatted shipping address from customer's profile.
        Returns a complete formatted address string.
        """
        if not self.customer:
            return "No customer associated with this order"

        try:
            profile = self.customer.customer_profile

            # Check if profile has address data
            if not profile.street_address:
                return "No address on file"

            # Build formatted address
            address_parts = []
            if profile.street_address:
                address_parts.append(profile.street_address)
            if profile.city:
                address_parts.append(profile.city)
            if profile.province:
                address_parts.append(profile.province)
            if profile.zip_code:
                address_parts.append(profile.zip_code)

            return ", ".join(address_parts) if address_parts else "Incomplete address"

        except Exception:
            return "Customer profile not found"

    def get_billing_address(self):
        """
        Retrieve billing address from customer's profile.
        Currently same as shipping address (can be extended later).
        """
        return self.get_shipping_address()

    def get_address_dict(self):
        """
        Return address as dictionary for API/JSON responses.
        Useful for order_details_api endpoint.
        """
        if not self.customer:
            return {
                "street": "",
                "city": "",
                "province": "",
                "zip_code": "",
                "full_address": "No customer",
            }

        try:
            profile = self.customer.customer_profile
            return {
                "street": profile.street_address or "",
                "city": profile.city or "",
                "province": profile.province or "",
                "zip_code": profile.zip_code or "",
                "full_address": self.get_shipping_address(),
            }
        except Exception:
            return {
                "street": "",
                "city": "",
                "province": "",
                "zip_code": "",
                "full_address": "Profile not found",
            }
    def is_paid(self):
        """Check if order is paid"""
        return self.payment_status == 'paid'
    
    def can_ship(self):
        """
        Check if order can be shipped based on payment method.
        GCash: Must be approved (paid) first
        COD: Can ship without payment (payment on delivery)
        """
        if self.payment_method == 'GCASH':
            return self.is_paid()
        return True  # COD can ship before payment
    
    def mark_payment_verified(self, verified_by_user):
        """
        Called when staff approves GCash payment.
        This happens when order status changes from 'Pending' to 'Processing'
        """
        if self.payment_method == 'GCASH':
            self.payment_status = 'paid'
            self.payment_verified_at = timezone.now()
            self.payment_verified_by = verified_by_user
            self.save(update_fields=['payment_status', 'payment_verified_at', 'payment_verified_by'])
    
    def mark_delivered_and_paid(self, verified_by_user=None):
        """
        Called when order is marked as 'Completed' (delivered).
        For COD orders, this also marks payment as received.
        For GCash orders, payment was already marked when approved.
        """
        if self.payment_method == 'COD' and self.payment_status == 'unpaid':
            self.payment_status = 'paid'
            self.payment_verified_at = timezone.now()
            self.payment_verified_by = verified_by_user
            self.save(update_fields=['payment_status', 'payment_verified_at', 'payment_verified_by'])

    # Convenience properties for template access (backward compatibility)
    @property
    def shipping_address(self):
        """Property for easy template access to shipping address."""
        return self.get_shipping_address()

    @property
    def billing_address(self):
        """Property for easy template access to billing address."""
        return self.get_billing_address()

    # ============================================================
    # ORDER CALCULATIONS
    # ============================================================

    @property
    def get_total_cost(self):
        """Calculates the total price of all items in the order."""
        if self.items.exists():
            return sum(item.item_total for item in self.items.all())
        return Decimal("0.00")

    # ============================================================
    # SAVE & DELETE
    # ============================================================

    def save(self, *args, **kwargs):
        """
        Auto-manage payment_status based on order status and payment method.
        
        GCASH:
            - Pending → unpaid
            - Processing/Shipped/Completed → paid
            - Returned → partially_refunded
            - Canceled → refunded

        COD:
            - Completed → paid
            - Returned → partially_refunded
            - Canceled → refunded
        """
        
        # Generate order_id if new
        if not self.order_id:
            self.order_id = generate_unique_order_id()

        # --- GCash Logic ---
        if self.payment_method == 'GCASH':
            if self.status in ['Processing', 'Shipped', 'Completed']:
                if self.payment_status in ['unpaid', 'partially_refunded', 'refunded']:
                    self.payment_status = 'paid'
                    if not self.payment_verified_at:
                        self.payment_verified_at = timezone.now()

            elif self.status == 'Pending':
                if self.payment_status == 'paid':
                    self.payment_status = 'unpaid'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

            elif self.status == 'Canceled':
                if self.payment_status in ['paid', 'partially_refunded','unpaid',]:
                    self.payment_status = 'refunded'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

            elif self.status == 'Returned':
                if self.payment_status in ['paid', 'partially_refunded','unpaid',]:
                    self.payment_status = 'partially_refunded'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

        # --- COD Logic ---
        elif self.payment_method == 'COD':
            if self.status == 'Completed':
                if self.payment_status in ['unpaid', 'partially_refunded', 'refunded']:
                    self.payment_status = 'paid'
                    if not self.payment_verified_at:
                        self.payment_verified_at = timezone.now()

            elif self.status == 'Returned':
                if self.payment_status == 'paid':
                    self.payment_status = 'partially_refunded'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

            elif self.status == 'Canceled':
                if self.payment_status == 'paid':
                    self.payment_status = 'refunded'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

        super().save(*args, **kwargs)



    def delete(self, using=None, keep_parents=False):
        """Soft delete implementation."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        """Restore soft-deleted order."""
        self.is_deleted = False
        self.deleted_at = None
        self.save()

    def __str__(self):
        customer_name = self.get_customer_name()
        return f"Order {self.order_id} - {customer_name}"


class OrderItem(models.Model):
    """
    Represents a single item within an order.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="order_items"
    )
    quantity = models.PositiveIntegerField(default=1)
    price_at_order = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("order", "product_variant")
        ordering = ["added_at"]

    @property
    def item_total(self):
        """Calculates the total price for this specific order item."""
        return self.price_at_order * self.quantity

    def save(self, *args, **kwargs):
        # Set price_at_order if not already set or is 0
        if not self.price_at_order or self.price_at_order == Decimal("0.00"):
            if self.product_variant:
                self.price_at_order = (
                    self.product_variant.price
                    or (
                        self.product_variant.product.price
                        if self.product_variant.product
                        else Decimal("0.00")
                    )
                    or Decimal("0.00")
                )
            else:
                self.price_at_order = Decimal("0.00")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} x {self.product_variant.product.name} ({self.product_variant.sku or 'Default'})"


# ============================================================
# MANUAL ORDERS (B2B, Phone Orders, etc.)
# These orders CAN store addresses directly since they may not
# have associated CustomerProfile records.
# ============================================================


def generate_unique_manual_order_id():
    """Generate unique manual order ID with MAN prefix"""
    while True:
        order_id = "MAN" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        if not ManualOrder.objects.filter(manual_order_id=order_id).exists():
            return order_id


class ManualOrder(models.Model):
    """
    Manual orders created by staff for B2B clients, phone orders, etc.

    ARCHITECTURE NOTE:
    - These orders KEEP their address fields since they may not have CustomerProfile.
    - If customer is linked, we can optionally pull from profile, but manual entry is allowed.
    - This separation keeps B2B/manual orders flexible while e-commerce orders are strict.
    """

    STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Processing", "Processing"),
        ("Shipped", "Shipped"),
        ("Completed", "Completed"),
        ("Canceled", "Canceled"),
        ("Returned", "Returned"),
    ]

    PAYMENT_METHODS = [
        ("COD", "Cash on Delivery"),
        ("GCASH", "GCash Transfer"),
    ]

    ORDER_SOURCE_CHOICES = [
        ("b2b", "B2B Client"),
        ("phone", "Phone Order"),
        ("email", "Email Order"),
        ("walk_in", "Walk-in"),
        
    ]

    manual_order_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        default=generate_unique_manual_order_id,
    )

    # Customer info (can be without User account)
    customer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_orders",
        help_text="Link to user account if exists",
    )
    customer_name = models.CharField(
        max_length=255, help_text="Customer or company name"
    )
    customer_email = models.EmailField(blank=True, null=True)
    customer_phone = models.CharField(max_length=20, blank=True, null=True)

    # Order details
    order_source = models.CharField(
        max_length=20, choices=ORDER_SOURCE_CHOICES, default="b2b"
    )

    # Manual orders KEEP address fields (they may not have profiles)
    shipping_address = models.TextField(
        help_text="Direct address entry for manual orders"
    )
    billing_address = models.TextField(blank=True, null=True)

    payment_method = models.CharField(
        max_length=20, choices=PAYMENT_METHODS, default="COD"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")

    order_date = models.DateTimeField(default=timezone.now)
    expected_delivery_date = models.DateField(null=True, blank=True)

    notes = models.TextField(
        blank=True, null=True, help_text="Internal notes about this order"
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    gcash_reference_image = models.ImageField(
        upload_to="payment_proofs/gcash/manual/",  # Separate folder for organization
        null=True,
        blank=True,
        verbose_name="GCash Payment Proof",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_manual_orders",
        help_text="Staff who created this order",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    stock_deducted = models.BooleanField(default=False)
    stock_deducted_at = models.DateTimeField(null=True, blank=True)
    stock_restored = models.BooleanField(default=False)
    stock_restored_at = models.DateTimeField(null=True, blank=True)
    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('paid', 'Paid'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
    ]
    
    payment_status = models.CharField(
        max_length=25,
        choices=PAYMENT_STATUS_CHOICES,
        default='unpaid',
        help_text='Automatically managed based on payment method and order status'
    )
    
    payment_verified_at = models.DateTimeField(null=True, blank=True)
    
    payment_verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_manual_payments'
    )
    

    class Meta:
        ordering = ["-order_date"]
        verbose_name = "Manual Order"
        verbose_name_plural = "Manual Orders"

    def save(self, *args, **kwargs):
        if not self.manual_order_id:
            self.manual_order_id = generate_unique_manual_order_id()

        if not self.billing_address:
            self.billing_address = self.shipping_address
        
        # Auto-manage payment status based on order status and payment method
        # --- GCash Logic ---
        if self.payment_method == 'GCASH':
            if self.status in ['Processing', 'Shipped', 'Completed']:
                if self.payment_status in ['unpaid', 'partially_refunded', 'refunded']:
                    self.payment_status = 'paid'
                    if not self.payment_verified_at:
                        self.payment_verified_at = timezone.now()

            elif self.status == 'Pending':
                if self.payment_status == 'paid':
                    self.payment_status = 'unpaid'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

            elif self.status == 'Canceled':
                if self.payment_status in ['paid', 'partially_refunded', 'unpaid']:
                    self.payment_status = 'refunded'
                    self.payment_verified_at = None
                    self.payment_verified_by = None

            elif self.status == 'Returned':
                if self.payment_status in ['paid', 'partially_refunded', 'unpaid']:
                    self.payment_status = 'partially_refunded'
                    self.payment_verified_at = None
                    self.payment_verified_by = None
                        
        # --- COD Logic ---
        elif self.payment_method == 'COD':
            if self.status == 'Completed':
                if self.payment_status in ['unpaid', 'partially_refunded', 'refunded']:
                    self.payment_status = 'paid'
                    if not self.payment_verified_at:
                        self.payment_verified_at = timezone.now()
            else:
                # Keep as unpaid until delivered, or handle refunds
                if self.status == 'Returned':
                    if self.payment_status == 'paid':
                        self.payment_status = 'partially_refunded'
                        self.payment_verified_at = None
                        self.payment_verified_by = None
                elif self.status == 'Canceled':
                    if self.payment_status == 'paid':
                        self.payment_status = 'refunded'
                        self.payment_verified_at = None
                        self.payment_verified_by = None
                elif self.status != 'Completed':
                    # Revert to unpaid if status changes from Completed
                    if self.payment_status == 'paid':
                        self.payment_status = 'unpaid'
                        self.payment_verified_at = None
                        self.payment_verified_by = None
        
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        """Soft delete and restore stock"""
        if not self.is_deleted:
            self.is_deleted = True
            self.deleted_at = timezone.now()
            self.save()
            for item in self.items.all():
                variant = item.product_variant
                variant.product.stock_quantity += item.quantity
                variant.product.save()

    def restore(self):
        """Restore soft-deleted order and deduct stock"""
        if self.is_deleted:
            self.is_deleted = False
            self.deleted_at = None
            self.save()
            for item in self.items.all():
                variant = item.product_variant
                variant.product.stock_quantity -= item.quantity
                variant.product.save()

    def hard_delete(self, using=None, keep_parents=False):
        """Permanently delete the order"""
        super().delete(using=using, keep_parents=keep_parents)
    def is_paid(self):
        """Check if manual order is paid"""
        return self.payment_status == 'paid'
    
    def can_ship(self):
        """Check if order can be shipped based on payment method"""
        if self.payment_method == 'GCASH':
            return self.is_paid()
        return True  # COD can ship before payment
    
    def mark_payment_verified(self, verified_by_user):
        """Called when staff approves GCash payment"""
        if self.payment_method == 'GCASH':
            self.payment_status = 'paid'
            self.payment_verified_at = timezone.now()
            self.payment_verified_by = verified_by_user
            self.save(update_fields=['payment_status', 'payment_verified_at', 'payment_verified_by'])
    
    def mark_delivered_and_paid(self, verified_by_user=None):
        """Called when order is marked as delivered (for COD)"""
        if self.payment_method == 'COD' and self.payment_status == 'unpaid':
            self.payment_status = 'paid'
            self.payment_verified_at = timezone.now()
            self.payment_verified_by = verified_by_user
            self.save(update_fields=['payment_status', 'payment_verified_at', 'payment_verified_by'])
    


    @property
    def get_total_cost(self):
        """Calculate total cost of all items"""
        if self.items.exists():
            return sum(item.item_total for item in self.items.all())
        return Decimal("0.00")

    def get_customer_display(self):
        """Return customer name for display"""
        if self.customer:
            return self.customer.get_full_name() or self.customer.username
        return self.customer_name or "Unknown Customer"

    def __str__(self):
        return f"Manual Order {self.manual_order_id} - {self.get_customer_display()}"


class ManualOrderItem(models.Model):
    """Line items for manual orders."""

    order = models.ForeignKey(
        ManualOrder, on_delete=models.CASCADE, related_name="items"
    )
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="manual_order_items"
    )
    quantity = models.PositiveIntegerField(default=1)
    price_at_order = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["added_at"]
        unique_together = ("order", "product_variant")
        verbose_name = "Manual Order Item"
        verbose_name_plural = "Manual Order Items"

    @property
    def item_total(self):
        """Calculate total for this line item"""
        return self.price_at_order * self.quantity

    def save(self, *args, **kwargs):
        if not self.price_at_order or self.price_at_order == Decimal("0.00"):
            if self.product_variant:
                self.price_at_order = (
                    self.product_variant.price
                    or (
                        self.product_variant.product.price
                        if self.product_variant.product
                        else Decimal("0.00")
                    )
                    or Decimal("0.00")
                )
            else:
                self.price_at_order = Decimal("0.00")
        super().save(*args, **kwargs)

    def __str__(self):
        product_name = (
            self.product_variant.product.name
            if self.product_variant and self.product_variant.product
            else "Unknown"
        )
        variant_info = (
            f" ({self.product_variant.sku})"
            if self.product_variant and self.product_variant.sku
            else ""
        )
        return f"{self.quantity} x {product_name}{variant_info}"
# Add this to your apps/orders/models.py file

import string
import random
from django.db import models
from django.utils import timezone
from decimal import Decimal

def generate_unique_invoice_id():
    """Generate unique invoice ID with INV prefix"""
    while True:
        invoice_id = "INV" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        if not Invoice.objects.filter(invoice_id=invoice_id).exists():
            return invoice_id


class Invoice(models.Model):
    """
    Invoice Model - Generated when order payment is confirmed
    """
    invoice_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        default=generate_unique_invoice_id
    )
    
    # Link to either Order or ManualOrder (one will be null)
    order = models.OneToOneField(
        'Order',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='invoice',
        help_text='Link to customer order'
    )
    
    manual_order = models.OneToOneField(
        'ManualOrder',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='invoice',
        help_text='Link to manual order'
    )
    
    # Invoice details
    invoice_date = models.DateTimeField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)
    
    # Cached totals (for performance)
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    tax_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00')
    )
    
    # Status
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('issued', 'Issued'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='issued'
    )
    
    # Notes
    notes = models.TextField(blank=True, null=True)
    
    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_invoices'
    )
    
    class Meta:
        ordering = ['-invoice_date']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
    
    def save(self, *args, **kwargs):
        if not self.invoice_id:
            self.invoice_id = generate_unique_invoice_id()
        
        # Calculate totals from linked order
        if self.order:
            self.subtotal = self.order.get_total_cost
            self.total_amount = self.subtotal + self.tax_amount
        elif self.manual_order:
            self.subtotal = self.manual_order.get_total_cost
            self.total_amount = self.subtotal + self.tax_amount
        
        super().save(*args, **kwargs)
    
    def get_order(self):
        """Return the linked order (either Order or ManualOrder)"""
        return self.order or self.manual_order
    
    def get_customer_name(self):
        """Get customer name from linked order"""
        order = self.get_order()
        if not order:
            return "Unknown"
        
        if hasattr(order, 'customer') and order.customer:
            return order.customer.get_full_name() or order.customer.username
        elif hasattr(order, 'customer_name'):
            return order.customer_name
        return "Guest"
    
    def get_customer_email(self):
        """Get customer email from linked order"""
        order = self.get_order()
        if not order:
            return None
        
        if hasattr(order, 'customer') and order.customer:
            return order.customer.email
        elif hasattr(order, 'customer_email'):
            return order.customer_email
        return None
    
    def get_shipping_address(self):
        """Get shipping address from linked order"""
        order = self.get_order()
        if not order:
            return "No address"
        
        if hasattr(order, 'get_shipping_address'):
            return order.get_shipping_address()
        elif hasattr(order, 'shipping_address'):
            return order.shipping_address
        return "No address"
    
    def __str__(self):
        return f"Invoice {self.invoice_id} - {self.get_customer_name()}"