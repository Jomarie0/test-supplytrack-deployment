from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.inventory.models import Product, DemandCheckLog
from apps.inventory.utils.forecasting import get_monthly_forecast_for_reorder


class Command(BaseCommand):
    help = "Update demand forecasts for all active products"

    def add_arguments(self, parser):
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Show detailed error messages",
        )

    def handle(self, *args, **options):
        debug_mode = options.get("debug", False)
        products = Product.objects.filter(is_deleted=False, is_active=True)

        updated_count = 0
        error_count = 0
        no_sales_count = 0
        default_forecast_value = 10  # You can adjust this baseline

        for product in products:
            try:
                forecasted_qty, error = get_monthly_forecast_for_reorder(product.product_id)

                # Handle missing or invalid forecasts
                if error or forecasted_qty is None:
                    forecasted_qty = default_forecast_value
                    no_sales_count += 1

                    if debug_mode:
                        self.stdout.write(
                            self.style.WARNING(
                                f"⚠ {product.name}: {error or 'No forecast data — using default'}"
                            )
                        )

                current_stock = product.stock_quantity
                restock_needed = current_stock < forecasted_qty

                # Reuse recent log if within 24 hours
                recent_log = DemandCheckLog.objects.filter(
                    product=product,
                    is_deleted=False,
                    checked_at__gte=timezone.now() - timedelta(hours=24),
                ).first()

                if recent_log:
                    recent_log.forecasted_quantity = forecasted_qty
                    recent_log.current_stock = current_stock
                    recent_log.restock_needed = restock_needed
                    recent_log.checked_at = timezone.now()
                    recent_log.save()
                else:
                    # Soft delete older logs
                    DemandCheckLog.objects.filter(
                        product=product,
                        is_deleted=False
                    ).update(is_deleted=True, deleted_at=timezone.now())

                    # Create a fresh log
                    DemandCheckLog.objects.create(
                        product=product,
                        forecasted_quantity=forecasted_qty,
                        current_stock=current_stock,
                        restock_needed=restock_needed,
                    )

                updated_count += 1

                if error:
                    self.stdout.write(
                        self.style.WARNING(f"✓ {product.name}: used default forecast {forecasted_qty}")
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f"✓ {product.name}: forecasted {forecasted_qty}")
                    )

            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f"✗ Error with {product.name}: {str(e)}")
                )
                if debug_mode:
                    import traceback
                    traceback.print_exc()

        # Final summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\n=== Forecast Update Summary ===\n"
                f"Updated successfully: {updated_count}\n"
                f"Used default (no sales): {no_sales_count}\n"
                f"Errors: {error_count}\n"
                f"Total processed: {products.count()}"
            )
        )
