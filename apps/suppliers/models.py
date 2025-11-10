from django.db import models
from decimal import Decimal


class Supplier(models.Model):
    profile = models.OneToOneField("users.SupplierProfile", on_delete=models.CASCADE)

    # Analytics / Tracking Fields
    total_orders = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    last_order_date = models.DateField(null=True, blank=True)

    # Optional Automation
    auto_order_enabled = models.BooleanField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.profile.company_name
