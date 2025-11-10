import csv
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.inventory.models import Product, DemandCheckLog
from apps.suppliers.models import SupplierProfile  # adjust if your supplier model differs


class Command(BaseCommand):
    help = "Import inventory data from CSV and initialize forecast logs"

    def handle(self, *args, **kwargs):
        self.stdout.write("📦 Running inventory import...")

        with open("dummy_datas/dummy_inventory.csv", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            created_count = 0
            updated_count = 0
            skipped_count = 0

            for row in reader:
                supplier = None
                supplier_name = row.get("supplier")

                if supplier_name:
                    try:
                        supplier = SupplierProfile.objects.get(company_name=supplier_name)
                    except SupplierProfile.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(
                                f"⚠ Supplier '{supplier_name}' not found. Skipping product '{row.get('name')}'."
                            )
                        )
                        skipped_count += 1
                        continue

                product, created = Product.objects.update_or_create(
                    name=row["name"],
                    defaults={
                        "description": row.get("description", ""),
                        "supplier_profile": supplier,
                        "price": Decimal(row.get("price", "0") or "0"),
                        "cost_price": Decimal(row.get("cost_price", "0") or "0"),
                        "stock_quantity": int(row.get("stock_quantity", 0)),
                        "unit": row.get("unit", "") or "pcs",
                        "is_active": True,
                    },
                )

                # Create a DemandCheckLog entry with baseline forecast
                DemandCheckLog.objects.create(
                    product=product,
                    forecasted_quantity=int(row.get("forecasted_quantity", 10)),
                    current_stock=product.stock_quantity,
                    restock_needed=product.stock_quantity
                    < int(row.get("forecasted_quantity", 10)),
                    checked_at=timezone.now(),
                )

                if created:
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f"✅ Created product: {product.name}"))
                else:
                    updated_count += 1
                    self.stdout.write(f"♻️ Updated product: {product.name}")

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n=== Import Summary ===\n"
                    f"Created: {created_count}\n"
                    f"Updated: {updated_count}\n"
                    f"Skipped (missing supplier): {skipped_count}"
                )
            )
