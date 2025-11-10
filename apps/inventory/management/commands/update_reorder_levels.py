from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.inventory.models import Product, DemandCheckLog


class Command(BaseCommand):
    help = "Analyze and update product restock recommendations based on forecasted demand"

    def add_arguments(self, parser):
        parser.add_argument(
            "--safety-factor",
            type=float,
            default=1.5,
            help="Multiplier for safety stock threshold (default: 1.5)",
        )
        parser.add_argument(
            "--min-threshold",
            type=int,
            default=5,
            help="Minimum recommended stock threshold (default: 5)",
        )
        parser.add_argument(
            "--product-id",
            type=str,
            help="Run analysis for a specific product only",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without updating logs",
        )

    def handle(self, *args, **options):
        safety_factor = options["safety_factor"]
        min_threshold = options["min_threshold"]
        product_id = options.get("product_id")
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("🧪 DRY RUN MODE - No changes will be made"))

        # Filter products
        if product_id:
            products = Product.objects.filter(product_id=product_id, is_deleted=False)
            if not products.exists():
                self.stdout.write(self.style.ERROR(f"❌ Product with ID {product_id} not found"))
                return
        else:
            products = Product.objects.filter(is_deleted=False, is_active=True)

        updated_count = 0
        skipped_count = 0
        error_count = 0

        self.stdout.write(f"Analyzing {products.count()} products...\n")

        for product in products:
            try:
                # Get the latest forecast log
                latest_log = (
                    DemandCheckLog.objects.filter(product=product, is_deleted=False)
                    .order_by("-checked_at")
                    .first()
                )

                if not latest_log:
                    self.stdout.write(
                        self.style.WARNING(f"⚠ {product.name}: No forecast data found — skipping.")
                    )
                    skipped_count += 1
                    continue

                forecasted_qty = latest_log.forecasted_quantity or 0
                current_stock = latest_log.current_stock or product.stock_quantity
                recommended_threshold = max(min_threshold, round(forecasted_qty * safety_factor))
                restock_needed = current_stock < recommended_threshold

                # Print result
                self.stdout.write(
                    f"{'🚨' if restock_needed else '✅'} {product.name} "
                    f"(Current: {current_stock}, Forecast: {forecasted_qty}, "
                    f"Recommended: {recommended_threshold})"
                )

                if not dry_run:
                    # Update log if threshold logic changes
                    latest_log.restock_needed = restock_needed
                    latest_log.checked_at = timezone.now()
                    latest_log.save()
                    updated_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"✗ {product.name}: Unexpected error — {str(e)}")
                )
                error_count += 1

        # Final summary
        summary_text = (
            f"\n=== Restock Recommendation Summary ===\n"
            f"Updated logs: {updated_count}\n"
            f"Skipped (no forecast): {skipped_count}\n"
            f"Errors: {error_count}\n"
            f"Total processed: {products.count()}"
        )

        self.stdout.write(self.style.SUCCESS(summary_text))
