from django.db import models


class Delivery(models.Model):
    PENDING_DISPATCH = "pending_dispatch"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    FAILED = "failed"

    DELIVERY_STATUS_CHOICES = [
        (PENDING_DISPATCH, "Pending Dispatch"),
        (OUT_FOR_DELIVERY, "Out for Delivery"),
        (DELIVERED, "Delivered"),
        (FAILED, "Failed"),
    ]

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="delivery"
    )

    delivery_status = models.CharField(
        max_length=50, choices=DELIVERY_STATUS_CHOICES, default=PENDING_DISPATCH
    )

    delivered_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    proof_of_delivery_image = models.ImageField(
        upload_to="proofs_of_delivery/%Y/%m/%d/",  # Organizes files by date
        null=True,
        blank=True,
        help_text="Image proof that the delivery was successfully completed.",
    )

    # NEW FIELD: Optional text note from the delivery personnel
    delivery_note = models.TextField(
        max_length=500,
        null=True,
        blank=True,
        help_text="A short note about the delivery (e.g., left with neighbor).",
    )

    class Meta:
        verbose_name_plural = "Deliveries"

    def __str__(self):
        return f"Delivery for Order {self.order.order_id} - Status: {self.get_delivery_status_display()}"

    # REMOVED THE save() METHOD COMPLETELY
    # Let the signals handle all status synchronization
    # This prevents conflicts between model logic and signals
