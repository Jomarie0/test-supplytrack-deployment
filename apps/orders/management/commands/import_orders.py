import csv
from decimal import Decimal, InvalidOperation
from datetime import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.inventory.models import Product
from apps.store.models import ProductVariant
from apps.orders.models import Order, OrderItem

User = get_user_model()


class Command(BaseCommand):
    help = "Import orders data from CSV - creates Orders with OrderItems"

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='dummy_datas/dumm_new_orders.csv',
            help='Path to CSV file'
        )

    def handle(self, *args, **options):
        csv_file = options['file']
        self.stdout.write(f"Importing orders from {csv_file}...")

        try:
            with open(csv_file, newline="", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)
                orders_created = 0
                orders_failed = 0

                for row in reader:
                    try:
                        # Extract data from CSV
                        product_name = row.get("product_name")
                        quantity = int(row.get("quantity", 1))
                        unit_price = Decimal(row.get("unit_price", "0.00"))
                        order_date_str = row.get("order_date")
                        expected_delivery_str = row.get("expected_delivery")
                        status = row.get("status", "Pending")
                        customer_username = row.get("customer_username")
                        payment_method = row.get("payment_method", "COD")

                        # Get customer
                        try:
                            customer = User.objects.get(username=customer_username)
                        except User.DoesNotExist:
                            self.stdout.write(
                                self.style.ERROR(
                                    f"❌ User '{customer_username}' not found. Skipping."
                                )
                            )
                            orders_failed += 1
                            continue

                        # Get product
                        try:
                            product = Product.objects.get(name=product_name, is_deleted=False)
                        except Product.DoesNotExist:
                            self.stdout.write(
                                self.style.ERROR(
                                    f"❌ Product '{product_name}' not found. Skipping."
                                )
                            )
                            orders_failed += 1
                            continue

                        # Get or create product variant
                        variant = ProductVariant.objects.filter(product=product).first()
                        if not variant:
                            # Create default variant if none exists
                            variant = ProductVariant.objects.create(
                                product=product,
                                sku=f"{product.product_id}-DEFAULT",
                                price=product.price,
                                is_active=True
                            )

                        # Parse dates
                        try:
                            order_date = timezone.make_aware(
                                datetime.strptime(order_date_str, "%Y-%m-%d %H:%M:%S")
                            )
                        except (TypeError, ValueError):
                            order_date = timezone.now()

                        try:
                            expected_delivery = datetime.strptime(
                                expected_delivery_str, "%Y-%m-%d"
                            ).date()
                        except (TypeError, ValueError):
                            expected_delivery = None

                        # Validate status
                        valid_statuses = ["Pending", "Processing", "Shipped", "Completed", "Canceled", "Returned"]
                        if status not in valid_statuses:
                            status = "Completed"  # Default to Completed for historical data

                        # Create Order
                        order = Order.objects.create(
                            customer=customer,
                            payment_method=payment_method,
                            status=status,
                            order_date=order_date,
                            expected_delivery_date=expected_delivery,
                        )

                        # Create OrderItem
                        order_item = OrderItem.objects.create(
                            order=order,
                            product_variant=variant,
                            quantity=quantity,
                            price_at_order=unit_price,
                        )

                        # Deduct stock if order is completed
                        if status == "Completed" and not order.stock_deducted:
                            product.stock_quantity -= quantity
                            product.save()
                            order.stock_deducted = True
                            order.stock_deducted_at = timezone.now()
                            order.save()

                        orders_created += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"✓ Created order {order.order_id}: {quantity}x {product_name} "
                                f"for {customer.username} on {order_date.strftime('%Y-%m-%d')}"
                            )
                        )

                    except Exception as e:
                        orders_failed += 1
                        self.stdout.write(
                            self.style.ERROR(f"❌ Error processing row: {str(e)}")
                        )

                # Summary
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n=== Import Complete ===\n"
                        f"✓ Orders created: {orders_created}\n"
                        f"❌ Orders failed: {orders_failed}"
                    )
                )

        except FileNotFoundError:
            self.stdout.write(
                self.style.ERROR(f"❌ File not found: {csv_file}")
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"❌ Unexpected error: {str(e)}")
            )