# apps/purchasing/models.py

from django.db import models
from django.utils import timezone
import random
import string
from decimal import Decimal

from apps.inventory.models import Product
from apps.users.models import SupplierProfile


def generate_unique_purchase_order_id():
    """Generates a unique Purchase Order number."""
    while True:
        po_id = "PO" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )
        if not PurchaseOrder.objects.filter(purchase_order_id=po_id).exists():
            return po_id


class PurchaseOrder(models.Model):
    """Represents a purchase order placed with a supplier."""

    purchase_order_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        default=generate_unique_purchase_order_id,
    )
    supplier_profile = models.ForeignKey(
        SupplierProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="purchase_orders",
    )
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.PROTECT,
        related_name="created_pos",
        null=True,
        blank=True,
        help_text="Staff member who created the PO request.",
    )

    order_date = models.DateTimeField(default=timezone.now)
    expected_delivery_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)

    # ====================================================
    # STATUS CHOICES
    # ====================================================
    STATUS_DRAFT = "draft"
    STATUS_REQUEST_PENDING = "request_pending"
    STATUS_SUPPLIER_PRICED = "supplier_priced"
    STATUS_CONFIRMED = "confirmed"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_RECEIVED = "received"
    STATUS_CANCELLED = "cancelled"
    STATUS_PARTIALLY_RECEIVED = "partially_received"
    STATUS_REFUND = "refund"

    PO_STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft (Internal)"),
        (STATUS_REQUEST_PENDING, "Request Pending Price"),
        (STATUS_SUPPLIER_PRICED, "Supplier Price Submitted"),
        (STATUS_CONFIRMED, "Confirmed & Ready for Shipping"),
        (STATUS_IN_TRANSIT, "In Transit (OTW)"),
        (STATUS_RECEIVED, "Received & Completed"),
        (STATUS_PARTIALLY_RECEIVED, "Partially Received"),
        (STATUS_REFUND, "Refund/Return Requested"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    
    status = models.CharField(
        max_length=30, choices=PO_STATUS_CHOICES, default=STATUS_DRAFT
    )

    # ====================================================
    # PAYMENT FIELDS
    # ====================================================
    PAYMENT_CHOICES = [
        ("tbd", "To Be Determined"),
        ("cod", "Cash on Delivery (COD)"),
        ("net_30", "Net 30 Days (Pay Later)"),
        ("prepaid", "Pre-Paid"),
    ]
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_CHOICES,
        default="tbd",
        help_text="Final agreed upon payment method.",
    )
    
    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
    ]
    payment_status = models.CharField(
        max_length=25,
        choices=PAYMENT_STATUS_CHOICES,
        default='unpaid',
        help_text='Payment status - automatically managed'
    )

    payment_verified_at = models.DateTimeField(
        null=True, 
        blank=True,
        help_text='When payment was verified/confirmed'
    )

    payment_verified_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_po_payments',
        help_text='Staff who verified/approved the payment'
    )

    pay_later = models.BooleanField(default=False)
    payment_due_date = models.DateField(null=True, blank=True)
    
    # Payment proof image for prepaid and pay later methods
    payment_proof_image = models.ImageField(
        upload_to='payment_proofs/po/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text='Proof of payment (receipt, screenshot, etc.)'
    )

    # ====================================================
    # REFUND FIELDS
    # ====================================================
    refund_reason = models.TextField(
        blank=True, 
        null=True, 
        help_text="Reason for refund/return request"
    )
    refund_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal("0.00"),
        help_text="Amount to be refunded"
    )

    # ====================================================
    # OTHER FIELDS
    # ====================================================
    total_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"PO {self.purchase_order_id} - {self.supplier_profile.company_name if self.supplier_profile else 'No Supplier'}"

    def save(self, *args, **kwargs):
        """Enhanced save with automatic payment status management"""
        
        # Generate PO ID for new orders
        if not self.pk and not self.purchase_order_id:
            self.purchase_order_id = generate_unique_purchase_order_id()
        
        # Auto-calculate payment status
        self._update_payment_status()
        
        super().save(*args, **kwargs)

    def _update_payment_status(self):
        """
        Automatically updates payment status based on order status and payment terms.
        Logic priority (first match wins):
        1. Refund status
        2. Cancelled status
        3. Pay Later / Net 30 (special handling - doesn't auto-pay on receipt)
        4. Other payment methods (prepaid, COD)
        5. Default unpaid for early stages
        """
        today = timezone.now().date()
        
        # PRIORITY 1: Refund overrides everything
        if self.status == self.STATUS_REFUND:
            if self.refund_amount and self.refund_amount < self.total_cost:
                self.payment_status = 'partially_refunded'
            else:
                self.payment_status = 'refunded'
            return
        
        # PRIORITY 2: Cancelled orders reset payment
        if self.status == self.STATUS_CANCELLED:
            if self.payment_status in ['paid', 'overdue']:
                self.payment_status = 'unpaid'
                self.payment_verified_at = None
                self.payment_verified_by = None
            return
        
        # PRIORITY 3: Pay Later / Net 30 - Special Logic (DOESN'T auto-pay on receipt!)
        if self.payment_method == 'net_30' or self.pay_later:
            # Pay later requires payment proof to be marked as paid
            if self.payment_proof_image:
                # Has proof - mark as paid
                if self.payment_status != 'paid':
                    self.payment_status = 'paid'
                    if not self.payment_verified_at:
                        self.payment_verified_at = timezone.now()
            else:
                # No proof yet - check if overdue
                if self.payment_due_date and self.payment_due_date < today:
                    self.payment_status = 'overdue'
                else:
                    # Not yet due or no due date - keep as unpaid
                    if self.payment_status not in ['paid', 'refunded', 'partially_refunded']:
                        self.payment_status = 'unpaid'
            return  # Exit early - don't apply other rules
        
        # PRIORITY 4: Prepaid orders - require payment proof to be marked as paid
        if self.payment_method == 'prepaid':
            # Prepaid orders are paid only when payment proof is uploaded
            if self.payment_proof_image and self.status in [self.STATUS_CONFIRMED, self.STATUS_IN_TRANSIT, self.STATUS_PARTIALLY_RECEIVED, self.STATUS_RECEIVED]:
                if self.payment_status != 'paid':
                    self.payment_status = 'paid'
                    if not self.payment_verified_at:
                        self.payment_verified_at = timezone.now()
            else:
                # No payment proof or not yet confirmed - keep as unpaid
                if self.payment_status not in ['paid', 'refunded', 'partially_refunded']:
                    self.payment_status = 'unpaid'
        
        # PRIORITY 5: COD orders
        elif self.payment_method == 'cod':
            # COD is only paid on delivery - unpaid until then
            if self.status == self.STATUS_RECEIVED:
                self.payment_status = 'paid'
                if not self.payment_verified_at:
                    self.payment_verified_at = timezone.now()
            else:
                if self.payment_status not in ['paid', 'refunded', 'partially_refunded']:
                    self.payment_status = 'unpaid'
        
        # PRIORITY 6: TBD or other payment methods - auto-pay on receipt
        elif self.status == self.STATUS_RECEIVED:
            if self.payment_status not in ['refunded', 'partially_refunded']:
                self.payment_status = 'paid'
                if not self.payment_verified_at:
                    self.payment_verified_at = timezone.now()
        
        # PRIORITY 7: Early stage orders default to unpaid
        else:
            if self.status in [self.STATUS_DRAFT, self.STATUS_REQUEST_PENDING, self.STATUS_SUPPLIER_PRICED]:
                if self.payment_status not in ['paid', 'refunded', 'partially_refunded']:
                    self.payment_status = 'unpaid'
    def is_due_soon(self, days=7):
        """
        Check if a pay-later PO is due within the specified number of days.
        Returns True if unpaid and due within 'days' from today.
        """
        if not self.pay_later or not self.payment_due_date or self.payment_status != 'unpaid':
            return False
        
        from django.utils import timezone
        today = timezone.now().date()
        days_until_due = (self.payment_due_date - today).days
        
        return 0 <= days_until_due <= days

    def delete(self, using=None, keep_parents=False):
        """Soft delete implementation"""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()

    def restore(self):
        """Restore soft-deleted PO"""
        self.is_deleted = False
        self.deleted_at = None
        self.save()

    def calculate_total_cost(self):
        """Recalculate total cost from items"""
        total = sum(item.total_price for item in self.items.all())
        if self.total_cost != total:
            self.total_cost = total
            self.save(update_fields=["total_cost"])

    class Meta:
        verbose_name_plural = "Purchase Orders"
        ordering = ["-order_date"]


class PurchaseOrderItem(models.Model):
    """Represents a single item within a Purchase Order."""

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    product_name_text = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="purchase_order_items",
        null=True,
        blank=True,
    )
    product_variant = models.ForeignKey(
        "store.ProductVariant",
        on_delete=models.CASCADE,
        related_name="purchase_order_items",
        null=True,
        blank=True,
    )

    quantity_ordered = models.PositiveIntegerField()
    quantity_received = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    def __str__(self):
        variant_name = (
            self.product_variant.product.name
            if self.product_variant
            else self.product_name_text or "Custom Item"
        )
        return f"{self.quantity_ordered}x {variant_name} (PO: {self.purchase_order.purchase_order_id})"

    @property
    def total_price(self):
        quantity = self.quantity_ordered if self.quantity_ordered is not None else 0
        unit_cost = self.unit_cost if self.unit_cost is not None else Decimal("0.00")
        return quantity * unit_cost

    @property
    def is_fully_received(self):
        return self.quantity_received >= self.quantity_ordered

    def save(self, *args, **kwargs):
        if not self.unit_cost and self.product_variant:
            self.unit_cost = getattr(
                self.product_variant, "cost_price", None
            ) or getattr(self.product_variant, "price", Decimal("0.00"))
        super().save(*args, **kwargs)
        self.purchase_order.calculate_total_cost()

    def delete(self, *args, **kwargs):
        po = self.purchase_order
        super().delete(*args, **kwargs)
        po.calculate_total_cost()

    class Meta:
        verbose_name_plural = "Purchase Order Items"
        unique_together = ("purchase_order", "product_variant")
        ordering = ["product_variant__product__name"]


class PurchaseOrderNotification(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="notifications"
    )
    supplier_name = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=30)
    message = models.CharField(max_length=255)
    payment_due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PO Notif {self.purchase_order.purchase_order_id} - {self.status}"