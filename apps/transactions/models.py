from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils import timezone
from django.conf import settings

try:
    # Django 3.1+ generic JSON field
    from django.db.models import JSONField
except Exception:
    JSONField = models.JSONField  # fallback


class AuditLog(models.Model):
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_APPROVE = "approve"
    ACTION_CANCEL = "cancel"
    ACTION_LOGIN = "login"
    ACTION_LOGOUT = "logout"
    ACTION_CHOICES = [
        (ACTION_CREATE, "Create"),
        (ACTION_UPDATE, "Update"),
        (ACTION_DELETE, "Delete"),
        (ACTION_APPROVE, "Approve"),
        (ACTION_CANCEL, "Cancel"),
        (ACTION_LOGIN, "Login"),
        (ACTION_LOGOUT, "Logout"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    # Generic relation to the affected object
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    object_id = models.CharField(max_length=255, null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    object_repr = models.TextField(blank=True)

    # optional structured data: before/after or metadata
    changes = JSONField(null=True, blank=True)
    extra = JSONField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_path = models.CharField(max_length=512, null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["user"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.timestamp.isoformat()} {self.user or 'system'} {self.action} {self.object_repr or self.object_id}"


class Transaction(models.Model):
    TRANSACTION_TYPES = [
        ("user_registration", "User Registered"),
        ("supplier_approval", "Supplier Approved"),
        ("order_placed", "Order Placed"),
        ("order_status_change", "Order Status Changed"),
        ("payment_received", "Payment Received"),
        ("delivery_completed", "Delivery Completed"),
        # Add more basic types as your system evolves
    ]

    user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="transactions",
        help_text="The user primarily associated with this transaction.",
    )

    # Optional: If you have an Order model, uncomment and adjust the import.
    # This links transactions directly to specific orders.
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        help_text="The order related to this transaction, if any.",
    )

    transaction_type = models.CharField(
        max_length=50,
        choices=TRANSACTION_TYPES,
        help_text="Type of action or event recorded.",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Amount involved in the transaction (e.g., payment, order total).",
    )
    timestamp = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the transaction occurred."
    )
    description = models.TextField(
        blank=True, help_text="A brief explanation or context for the transaction."
    )

    class Meta:
        ordering = ["-timestamp"]  # Most recent transactions first
        verbose_name = "Transaction History"
        verbose_name_plural = "Transaction Histories"

    def __str__(self):
        amount_str = f"₱{self.amount:,.2f}" if self.amount is not None else ""
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.get_transaction_type_display()} by {self.user.username if self.user else 'N/A'} {amount_str}"


def log_audit(
    user=None, action="update", instance=None, changes=None, request=None, extra=None
):
    """
    Create an AuditLog entry.

    - user: request.user or None
    - action: one of ACTION_* values
    - instance: model instance changed (optional)
    - changes: dict with before/after or summary
    - request: Django request (optional) for IP/path
    - extra: any extra JSON-serializable metadata
    """
    ct = None
    obj_id = None
    obj_repr = ""
    if instance is not None:
        try:
            ct = ContentType.objects.get_for_model(instance.__class__)
        except Exception:
            ct = None
        obj_id = getattr(instance, "pk", None)
        try:
            obj_repr = str(instance)
        except Exception:
            obj_repr = f"{instance.__class__.__name__}:{obj_id}"

    ip = None
    path = None
    if request is not None:
        ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR"))
        path = getattr(request, "path", None)

    try:
        AuditLog.objects.create(
            user=(user if getattr(user, "is_authenticated", False) else None),
            action=action,
            content_type=ct,
            object_id=str(obj_id) if obj_id is not None else None,
            object_repr=obj_repr,
            changes=changes,
            extra=extra,
            ip_address=ip,
            request_path=path,
        )
    except Exception:
        # swallow errors to avoid breaking business flows
        pass
