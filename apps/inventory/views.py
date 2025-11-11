from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError  # Import to handle foreign key errors

from .models import Product, DemandCheckLog, StockMovement, Category
from apps.users.models import SupplierProfile
from .forms import ProductForm, StockMovementForm
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from django.db.models.functions import TruncMonth
from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField

# IMPORTANT: Adjust these timezone imports
import datetime  # <--- Keep this for datetime.datetime if you construct dates manually
from django.utils import (
    timezone as django_timezone,    
)  # <--- Alias Django's timezone module!

# Imports for forecasting
from sklearn.linear_model import LinearRegression
import pandas as pd
import numpy as np
from datetime import timedelta  # Keep this as it's datetime.timedelta
from decimal import Decimal
from django.contrib import messages
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from django.views.decorators.http import require_GET

from datetime import datetime

# Corrected Imports for Order and OrderItem
from apps.orders.models import Order, OrderItem, ManualOrder, ManualOrderItem
from apps.delivery.models import Delivery
from .forms import CategoryForm
from apps.transactions.models import log_audit  # added
from apps.transactions.utils import compute_instance_diff  # added (ensure this line exists)
from django.db import transaction  # ensure transaction imported (already used)


# Your existing dashboard views (manager_dashboard, staff_dashboard)
@login_required
def manager_dashboard(request):
    return render(request, "inventory/manager/manager_dashboard.html")


@login_required
def staff_dashboard(request):
    return render(request, "inventory/admin/admin_dashboard.html")


@login_required
def product_list(request):
    """
    New view to display the main product inventory list (Read operation).
    Replaces the old 'inventory_list' as the primary list view.
    """
    # Use the custom active manager to fetch active, non-deleted products
    products = (
        Product.objects.filter(is_deleted=False)
        .select_related(
            "category",
            "supplier_profile",
            "supplier_profile__user",
        )
        .order_by("-updated_at", "-created_at")
    )
    from django.db.models import Prefetch
    products = products.prefetch_related(
        Prefetch(
            'demandchecklog_set',
            queryset=DemandCheckLog.objects.filter(is_deleted=False).order_by('-checked_at'),
            to_attr='latest_forecasts'
        )
    )
    
    # Calculate forecast for each product using the same method as dashboard
    from apps.orders.models import OrderItem, ManualOrderItem
    from django.db.models import Sum
    import pandas as pd
    import numpy as np
    from sklearn.linear_model import LinearRegression
    
    for product in products:
        # Try to get from DemandCheckLog first
        if product.latest_forecasts:
            product.latest_forecast = product.latest_forecasts[0]
        else:
            product.latest_forecast = None
        
        # If no forecast or forecast is 0, calculate it dynamically (next month forecast like dashboard)
        if not product.latest_forecast or product.latest_forecast.forecasted_quantity == 0:
            try:
                # Get sales data (same as demand_forecast API)
                customer_sales_data = (
                    OrderItem.objects.filter(
                        product_variant__product=product,
                        order__is_deleted=False,
                        order__status="Completed",
                    )
                    .values("order__order_date")
                    .annotate(total_quantity=Sum("quantity"))
                    .order_by("order__order_date")
                )
                
                manual_sales_data = (
                    ManualOrderItem.objects.filter(
                        product_variant__product=product,
                        order__is_deleted=False,
                        order__status="Completed",
                    )
                    .values("order__order_date")
                    .annotate(total_quantity=Sum("quantity"))
                    .order_by("order__order_date")
                )
                
                # Combine both datasets
                all_sales_data = {}
                for entry in customer_sales_data:
                    date = entry["order__order_date"]
                    if date not in all_sales_data:
                        all_sales_data[date] = 0
                    all_sales_data[date] += entry["total_quantity"] or 0
                
                for entry in manual_sales_data:
                    date = entry["order__order_date"]
                    if date not in all_sales_data:
                        all_sales_data[date] = 0
                    all_sales_data[date] += entry["total_quantity"] or 0
                
                if all_sales_data:
                    # Convert to DataFrame and calculate next month forecast
                    df_data = [
                        {"order__order_date": date, "total_quantity": qty}
                        for date, qty in all_sales_data.items()
                    ]
                    df = pd.DataFrame(df_data)
                    df["order__order_date"] = (
                        pd.to_datetime(df["order__order_date"])
                        .dt.tz_localize(None)
                        .dt.to_period("M")
                    )
                    df = df.groupby("order__order_date").sum().reset_index()
                    
                    # Add time index for regression
                    df["time_index"] = np.arange(len(df))
                    X = df[["time_index"]].values
                    y = df["total_quantity"].values
                    
                    model = LinearRegression()
                    model.fit(X, y)
                    
                    # Forecast 6 months ahead to get the array, but we only use the first month
                    future_months = 6
                    future_index = np.arange(len(df), len(df) + future_months).reshape(-1, 1)
                    forecast = model.predict(future_index)
                    
                    # Get only the FIRST month's forecast (next month), not the sum
                    # This matches what the dashboard shows for "next month"
                    first_month_forecast = max(0, round(forecast[0]))
                    
                    # Create a mock DemandCheckLog object for display
                    class MockForecast:
                        def __init__(self, qty):
                            self.forecasted_quantity = qty
                            self.restock_needed = product.stock_quantity < qty
                    
                    product.latest_forecast = MockForecast(first_month_forecast)
            except Exception as e:
                # If calculation fails, keep the existing forecast (or None)
                pass

    context = {
        "products": products,
        "page_title": "Inventory Stock List",
    }

    return render(
        request, "inventory/inventory_list/product/product_list.html", context
    )

from django.urls import reverse
from .forms import ProductVariantFormset  # <-- The Formset is essential


def product_create(request):
    """
    Handles the creation of a new Product and its related Product Variants
    using a Django Formset.
    """
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)
        # Binds the formset to the POST data
        formset = ProductVariantFormset(request.POST, request.FILES)

        # Use a transaction to ensure either BOTH Product and Variants save, or NEITHER saves.
        try:
            with transaction.atomic():
                if form.is_valid() and formset.is_valid():

                    # 1. Save the main Product
                    product_before = None
                    product = form.save(commit=False)
                    product.save()

                    # 2. Save the Product Variants using the Formset
                    formset.instance = product
                    formset.save()

                    messages.success(
                        request,
                        f'Product "{product.name}" and its variants were created successfully.',
                    )
                    # compute small diff (new object -> record created)
                    try:
                        tracked = [
                            "name",
                            "sku",
                            "price",
                            "cost_price",
                            "stock_quantity",
                            "is_active",
                            "description",
                        ]
                        changes = compute_instance_diff(
                            product_before, product, fields=tracked
                        )
                        if not changes:
                            changes = {"product_created": True}

                        def _log_create():
                            try:
                                log_audit(
                                    user=request.user,
                                    action="create",
                                    instance=product,
                                    changes=changes,
                                    request=request,
                                )
                            except Exception:
                                pass

                        transaction.on_commit(_log_create)
                    except Exception:
                        pass
                    return redirect(reverse("inventory:product_list"))

                else:
                    # If forms are invalid, transaction is not used, and error messages are set.
                    # Formset errors must be displayed clearly in the template.
                    messages.error(
                        request,
                        "Please correct the errors in the form and product variants.",
                    )

        except Exception as e:
            # Catch database or external errors (e.g., forecast API failure on save)
            messages.error(
                request, f"A critical error occurred while saving the product: {e}"
            )

    else:
        # GET request: Render empty forms
        form = ProductForm()
        formset = ProductVariantFormset()  # Unbound formset

    context = {
        "form": form,
        "formset": formset,
        "category_form": CategoryForm(),
        "is_edit": False,  # Flag for template logic (e.g., button labels)
        "page_title": "Add New Product",
        'categories': Category.objects.active().order_by("name"),
        'suppliers': SupplierProfile.objects.all(),
        
    }

    # Renders the new product_form.html template
    return render(
        request, "inventory/inventory_list/product/product_form.html", context
    )

from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from .models import Product
    
def toggle_product_active(request, product_id):
    """
    Toggle a product's active status (True/False) and log the change to audit (with user-friendly text).
    """
    product = get_object_or_404(Product, product_id=product_id, is_deleted=False)

    try:
        with transaction.atomic():
            old_status = "Active" if product.is_active else "Inactive"
            product.is_active = not product.is_active
            new_status = "Active" if product.is_active else "Inactive"
            product.save()

            # More human-readable audit entry
            changes = {
                "Product Status": f"{old_status} → {new_status}"
            }

            # Schedule audit log
            def _log_toggle():
                try:
                    log_audit(
                        user=request.user,
                        action="update",
                        instance=product,
                        changes=changes,
                        request=request,
                    )
                except Exception:
                    pass

            transaction.on_commit(_log_toggle)

            messages.success(
                request,
                f"Product '{product.name}' is now {new_status}.",
            )

    except Exception as e:
        messages.error(request, f"An error occurred while toggling product status: {e}")

    return redirect("inventory:product_list")

@csrf_exempt
def category_create_ajax(request):
    """
    Handles AJAX POST request to create a new category and returns JSON
    for updating the product dropdown.
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {"success": False, "error": "Invalid JSON format"}, status=400
            )

        # The form now expects data for 'name', 'parent', and 'description'
        form = CategoryForm(data)

        if form.is_valid():
            category = form.save()
            # Success: Use str(category) which calls get_full_path()
            return JsonResponse(
                {
                    "success": True,
                    "id": category.pk,
                    "name": str(category),  # e.g., 'Groceries → Beverages'
                }
            )
        else:
            # Failure: Return validation errors
            return JsonResponse(
                {"success": False, "errors": dict(form.errors.items())}, status=400
            )

    return JsonResponse(
        {"success": False, "error": "Only POST method allowed"}, status=405
    )


from django.shortcuts import get_object_or_404

# ... (Keep all your existing imports)

# ... (Existing views like product_list, product_create, dashboard, APIs, etc.)

# ==============================================================================
# NEW PRODUCT CRUD VIEWS (Full Page, Formset-based)
# ==============================================================================

# ... (product_list and product_create views defined above)


def product_update(request, pk):
    """
    Handles the update of an existing Product and its related Product Variants
    using a Django Formset.
    """
    # Retrieve the product instance or raise a 404 error
    product = get_object_or_404(Product, pk=pk, is_deleted=False)

    if request.method == "POST":
        # Bind the forms to the POST data and the existing instance
        form = ProductForm(request.POST, request.FILES, instance=product)
        formset = ProductVariantFormset(request.POST, request.FILES, instance=product)

        try:
            with transaction.atomic():
                if form.is_valid() and formset.is_valid():

                    # Take DB snapshot BEFORE applying changes
                    try:
                        product_before = Product.objects.get(pk=product.pk)
                    except Product.DoesNotExist:
                        product_before = None

                    # Save the main Product and variants
                    product = form.save()
                    formset.save()

                    messages.success(
                        request,
                        f'Product "{product.name}" and its variants were updated successfully.',
                    )

                    # compute field-level diff and schedule audit after commit
                    try:
                        tracked = [
                            "name",
                            "sku",
                            "price",
                            "cost_price",
                            "stock_quantity",
                            "is_active",
                            "description",
                        ]
                        changes = compute_instance_diff(product_before, product, fields=tracked)
                        if not changes:
                            changes = {"product_updated": True}

                        def _log_update():
                            try:
                                log_audit(
                                    user=request.user,
                                    action="update",
                                    instance=product,
                                    changes=changes,
                                    request=request,
                                )
                            except Exception:
                                pass

                        transaction.on_commit(_log_update)
                    except Exception:
                        pass

                    return redirect(reverse("inventory:product_list"))

                else:
                    messages.error(
                        request,
                        "Please correct the errors in the form and product variants.",
                    )

        except Exception as e:
            messages.error(
                request, f"A critical error occurred while updating the product: {e}"
            )

    else:
        form = ProductForm(instance=product)
        formset = ProductVariantFormset(instance=product)

    context = {
        "form": form,
        "formset": formset,
        "product": product,
        "category_form": CategoryForm(),
        "is_edit": True,
        "categories": Category.objects.active().order_by("name"),
        "page_title": f"Edit Product: {product.name}",
        "suppliers": SupplierProfile.objects.all(),
        
    }

    return render(
        request, "inventory/inventory_list/product/product_form.html", context
    )


@login_required
def product_archive_list(request):
    """
    Displays the list of products marked as soft-deleted.
    """
    # Filter for products where is_deleted is True
    # FIX: Add select_related to efficiently fetch supplier and user data
    archived_products = (
        Product.objects.filter(is_deleted=True)
        .select_related(
            "category",
            "supplier_profile",  # Selects the SupplierProfile
            "supplier_profile__user",  # Selects the related User
        )
        .order_by("-deleted_at")
    )  # Order by delete date is good for archive

    context = {
        "products": archived_products,
        "page_title": "Archived Inventory Products",
    }

    # NOTE: We use the new template name here
    return render(
        request, "inventory/inventory_list/product/product_archive_list.html", context
    )


@csrf_exempt
def delete_products(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ids = data.get("ids", [])

            if not ids:
                return JsonResponse(
                    {"status": "error", "message": "No product IDs provided."},
                    status=400,
                )

            products_to_delete = Product.objects.filter(id__in=ids)
            count = products_to_delete.count()  # Get count *before* deleting
            product_names = list(products_to_delete.values_list("name", flat=True))

            # Call .delete() on each instance to trigger the soft-delete logic
            for product in products_to_delete:
                product.delete()
            try:
                log_audit(
                    user=request.user,
                    action="delete",
                    instance=None,
                    changes={"deleted_products": product_names},
                    request=request,
                )
            except Exception:
                pass
            return JsonResponse(
                {
                    "status": "success",
                    "message": f"Successfully archived {product_names} {count} product(s).",
                    "product_names": product_names,
                    "ids": ids,
                }
            )

        except json.JSONDecodeError:
            return JsonResponse(
                {"status": "error", "message": "Invalid JSON format."}, status=400
            )
        except Exception as e:
            # Catch all other errors
            return JsonResponse(
                {
                    "status": "error",
                    "message": f"An unexpected error occurred: {str(e)}",
                },
                status=500,
            )

    return JsonResponse(
        {"status": "error", "message": "Invalid request method."}, status=405
    )


# --- CHECK YOUR OTHER VIEWS FOR CONSISTENCY ---


@csrf_exempt
def restore_products(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ids = data.get("ids", [])

            if not ids:
                return JsonResponse(
                    {"status": "error", "message": "No product IDs provided."},
                    status=400,
                )

            products_to_restore = Product.objects.filter(id__in=ids, is_deleted=True)
            count = products_to_restore.count()
            product_names = list(products_to_restore.values_list("name", flat=True))

            for product in products_to_restore:
                product.restore()
            try:
                log_audit(
                    user=request.user,
                    action="update",
                    instance=None,
                    changes={"restored_products": product_names},
                    request=request,
                )
            except Exception:
                pass
            return JsonResponse(
                {
                    "status": "success",
                    "message": f"Successfully restored {count} product(s).",
                    "product_names": product_names,
                }
            )
        except Exception as e:
            return JsonResponse(
                {
                    "status": "error",
                    "message": f"An error occurred during restore: {str(e)}",
                },
                status=500,
            )
    return JsonResponse(
        {"status": "error", "message": "Invalid request method."}, status=405
    )


@csrf_exempt
def permanently_delete_products(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ids = data.get("ids", [])

            if not ids:
                return JsonResponse(
                    {"status": "error", "message": "No product IDs provided."},
                    status=400,
                )

            # NOTE: We use .filter().delete() here for permanent deletion
            # The soft delete flag 'is_deleted=True' ensures we only delete archived items
            products_to_delete = Product.objects.filter(id__in=ids, is_deleted=True)
            product_names = list(products_to_delete.values_list("name", flat=True))

            deleted_count, _ = Product.objects.filter(
                id__in=ids, is_deleted=True
            ).delete()
            try:
                log_audit(
                    user=request.user,
                    action="delete",
                    instance=None,
                    changes={
                        "permanently_deleted_products": product_names,
                        "count": deleted_count,
                    },
                    request=request,
                )
            except Exception:
                pass
            return JsonResponse(
                {
                    "status": "success",
                    "message": f"Successfully and permanently deleted {deleted_count} product(s).",
                    "count": deleted_count,
                    "product_names": product_names,  # Send ALL names collected before deletion
                }
            )
        except IntegrityError:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "Cannot permanently delete one or more products because they are still linked to active orders or other records. You must manually address these links first.",
                },
                status=409,
            )
        except Exception as e:
            return JsonResponse(
                {
                    "status": "error",
                    "message": f"An unexpected error occurred: {str(e)}",
                },
                status=500,
            )

    return JsonResponse(
        {"status": "error", "message": "Invalid request method."}, status=405
    )


@login_required
def inventory_list(request):
    products = Product.objects.filter(is_deleted=False)
    movement_form = StockMovementForm()
    
    # Add stock level class to each product
    # for product in products:
    #     if product.stock_quantity <= de.reorder_level:
    #         product.stock_level_class = "stock-critical"
    #     elif product.stock_quantity <= product.reorder_level * 2:
    #         product.stock_level_class = "stock-low"
    #     elif product.stock_quantity <= product.reorder_level * 4:
    #         product.stock_level_class = "stock-medium"
    #     else:
    #         product.stock_level_class = "stock-high"

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)

        if form.is_valid():
            product = form.save()

            # Auto-create default variant if none exists
            from apps.store.models import ProductVariant

            if not product.variants.exists():
                ProductVariant.objects.create(
                    product=product,
                    sku=f"{product.product_id}-DEFAULT",
                    price=product.price,
                    is_active=True,
                )

            messages.success(request, "Product successfully added!")
            return redirect("inventory:inventory_list")
        else:
            messages.error(request, "Error adding product. Please check the form.")
            print(form.errors)
    else:
        form = ProductForm()

    categories = Category.objects.filter(is_active=True)
    supplier_profiles = SupplierProfile.objects.all()

    context = {
        "products": products,
        "form": form,
        "movement_form": movement_form,
        "categories": categories,
        "supplier_profiles": supplier_profiles,
    }
    return render(request, "inventory/inventory_list/inventory_list.html", context)


@login_required
@csrf_exempt
def update_product(request, product_id):
    """
    Dedicated view for updating a product, including supplier and variants.
    """
    from django.shortcuts import get_object_or_404
    from apps.store.models import ProductVariant

    product = get_object_or_404(Product, pk=product_id, is_deleted=False)
    supplier_profiles = SupplierProfile.objects.all()

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)

        if form.is_valid():
            product = form.save(commit=False)

            # Update supplier if selected
            supplier_profile_id = request.POST.get("supplier_profile")
            if supplier_profile_id:
                try:
                    product.supplier_profile = SupplierProfile.objects.get(
                        id=supplier_profile_id
                    )
                except SupplierProfile.DoesNotExist:
                    product.supplier_profile = None
            else:
                product.supplier_profile = None

            product.save()

            # Handle variant data
            variant_id = request.POST.get("variant_id", "").strip()
            variant_size = request.POST.get("variant_size", "").strip()
            variant_color = request.POST.get("variant_color", "").strip()
            variant_sku = request.POST.get("variant_sku", "").strip()
            variant_price = request.POST.get("variant_price", "").strip()

            try:
                variant_price_decimal = (
                    Decimal(variant_price) if variant_price else None
                )
            except:
                variant_price_decimal = None

            existing_variant = product.variants.first()

            if existing_variant:
                existing_variant.size = variant_size or None
                existing_variant.color = variant_color or None
                existing_variant.sku = variant_sku or existing_variant.sku
                existing_variant.price = variant_price_decimal or product.price
                existing_variant.save()
            else:
                ProductVariant.objects.create(
                    product=product,
                    size=variant_size or None,
                    color=variant_color or None,
                    sku=variant_sku or f"{product.product_id}-DEFAULT",
                    price=variant_price_decimal or product.price,
                    is_active=True,
                )

            messages.success(request, f"Product '{product.name}' successfully updated!")
            try:
                log_audit(
                    user=request.user,
                    action="update",
                    instance=product,
                    changes={"product_updated": True},
                    request=request,
                )
            except Exception:
                pass
            return redirect("inventory:inventory_list")
        else:
            messages.error(request, "Error updating product. Please check the form.")
            print("FORM ERRORS:", form.errors)

    # On GET or if POST failed, render the form with suppliers
    return render(
        request,
        "inventory/partials/update_product_modal.html",
        {
            "product": product,
            "form": ProductForm(instance=product),
            "supplier_profiles": supplier_profiles,
        },
    )


@login_required
def archive_list(request):
    archived_products = Product.objects.filter(is_deleted=True)
    return render(
        request,
        "inventory/inventory_list/archive_list.html",
        {"products": archived_products},
    )


# ---------------------- D A S H  B O A R D ------------------------- #


# Update the dashboard function around line 249
@login_required
def dashboard(request):
    """
    Main dashboard view with all necessary data for charts and statistics
    """
    # BASIC STATISTICS
    total_products = Product.objects.count()
    low_stock_count = DemandCheckLog.objects.filter(
        restock_needed=True, is_deleted=False
    ).count()
    orders = (
        Order.objects.filter(is_deleted=False)
        .select_related("customer__customer_profile")
        .prefetch_related("items__product_variant__product")
    )
    total_orders = orders.count()

    # Calculate current month's revenue (INCLUDING MANUAL ORDERS)
    current_month_start = django_timezone.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    # Revenue from customer orders
    customer_revenue = OrderItem.objects.filter(
        order__is_deleted=False,
        order__status="Completed",
        order__order_date__gte=current_month_start,
    ).aggregate(
        total=Sum(
            ExpressionWrapper(
                F("quantity") * F("price_at_order"), output_field=DecimalField()
            )
        )
    )[
        "total"
    ] or Decimal(
        "0.00"
    )

    # Revenue from manual orders
    manual_revenue = ManualOrderItem.objects.filter(
        order__is_deleted=False,
        order__status="Completed",
        order__order_date__gte=current_month_start,
    ).aggregate(
        total=Sum(
            ExpressionWrapper(
                F("quantity") * F("price_at_order"), output_field=DecimalField()
            )
        )
    )[
        "total"
    ] or Decimal(
        "0.00"
    )

    monthly_revenue = customer_revenue + manual_revenue

    # STOCK DATA
    products_for_chart = list(Product.objects.values_list("name", flat=True).filter(is_deleted=False))
    stock_quantities_for_chart = list(
        Product.objects.values_list("stock_quantity", flat=True).filter(is_deleted=False)
    )
    product_names_distinct = Product.objects.values_list("name", flat=True).distinct().filter(is_deleted=False)

    # SALES DATA - Monthly sales trend (INCLUDING MANUAL ORDERS)
    # Customer orders
    customer_sales_by_month = (
        Order.objects.filter(status="Completed", order_date__isnull=False)
        .annotate(month=TruncMonth("order_date"))
        .values("month")
        .annotate(
            total_revenue=Sum(
                F("items__price_at_order") * F("items__quantity"),
                output_field=DecimalField(),
            )
        )
        .order_by("month")
    )

    # Manual orders
    manual_sales_by_month = (
        ManualOrder.objects.filter(status="Completed", order_date__isnull=False)
        .annotate(month=TruncMonth("order_date"))
        .values("month")
        .annotate(
            total_revenue=Sum(
                F("items__price_at_order") * F("items__quantity"),
                output_field=DecimalField(),
            )
        )
        .order_by("month")
    )

    # Combine both sales data
    all_sales_data = {}
    for entry in customer_sales_by_month:
        month = entry["month"]
        if month not in all_sales_data:
            all_sales_data[month] = Decimal("0.00")
        all_sales_data[month] += entry["total_revenue"] or Decimal("0.00")

    for entry in manual_sales_by_month:
        month = entry["month"]
        if month not in all_sales_data:
            all_sales_data[month] = Decimal("0.00")
        all_sales_data[month] += entry["total_revenue"] or Decimal("0.00")

    months = [month.strftime("%b %Y") for month in sorted(all_sales_data.keys())]
    sales_totals = [
        float(all_sales_data[month]) for month in sorted(all_sales_data.keys())
    ]

    # ORDER STATUS DATA (INCLUDING MANUAL ORDERS)
    customer_status_counts = (
        Order.objects.filter(is_deleted=False)
        .values("status")
        .annotate(count=Count("id"))
    )

    manual_status_counts = (
        ManualOrder.objects.filter(is_deleted=False)
        .values("status")
        .annotate(count=Count("id"))
    )

    # Combine status counts
    status_counts = {}
    for entry in customer_status_counts:
        status = entry["status"]
        status_counts[status] = status_counts.get(status, 0) + entry["count"]

    for entry in manual_status_counts:
        status = entry["status"]
        status_counts[status] = status_counts.get(status, 0) + entry["count"]

    status_labels = list(status_counts.keys())
    status_counts_values = list(status_counts.values())

    # RECENT ORDERS (INCLUDING MANUAL ORDERS)
    recent_customer_orders = Order.objects.filter(is_deleted=False).order_by(
        "-order_date"
    )[:3]

    recent_manual_orders = ManualOrder.objects.filter(is_deleted=False).order_by(
        "-order_date"
    )[:3]

    # Combine and sort by date
    all_recent_orders = list(recent_customer_orders) + list(recent_manual_orders)
    all_recent_orders.sort(key=lambda x: x.order_date, reverse=True)
    recent_orders = all_recent_orders[:5]

    context = {
        # Statistics
        "total_products": total_products,
        "low_stock_count": low_stock_count,
        "total_orders": total_orders,
        "monthly_revenue": monthly_revenue.quantize(Decimal("0.01")),
        # Chart data (JSON serialized for JavaScript)
        "products_json": json.dumps(products_for_chart),
        "stock_quantities_json": json.dumps(stock_quantities_for_chart),
        "months_json": json.dumps(months),
        "sales_totals_json": json.dumps(sales_totals),
        "status_labels_json": json.dumps(status_labels),
        "status_counts_json": json.dumps(status_counts_values),
        # Raw data for template
        "product_names": product_names_distinct,
        "recent_orders": recent_orders,
    }

    return render(request, "inventory/admin/dashboards.html", context)


from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.db import transaction
from django.utils import timezone as django_timezone
from datetime import timedelta

@csrf_exempt
def product_forecast_api(request):
    """
    Forecast API - Checks ALL active products for next month forecast.
    Updates DemandCheckLog entries for products that need restocking.
    Uses the same calculation method as demand_forecast API for consistency.
    This is a batch update function that should be called periodically (e.g., daily).
    """
    from apps.orders.models import OrderItem, ManualOrderItem
    from django.db.models import Sum
    import pandas as pd
    import numpy as np
    from sklearn.linear_model import LinearRegression

    products = Product.objects.filter(is_deleted=False, is_active=True)

    updated_count = 0
    error_count = 0
    no_sales_count = 0
    restock_needed_count = 0
    results = []

    with transaction.atomic():
        for product in products:
            try:
                # ------------------------------------------------------------------
                # 1. Collect all sales data (customer + manual orders)
                # ------------------------------------------------------------------
                customer_sales_data = (
                    OrderItem.objects.filter(
                        product_variant__product=product,
                        order__is_deleted=False,
                        order__status="Completed",
                    )
                    .values("order__order_date")
                    .annotate(total_quantity=Sum("quantity"))
                    .order_by("order__order_date")
                )

                manual_sales_data = (
                    ManualOrderItem.objects.filter(
                        product_variant__product=product,
                        order__is_deleted=False,
                        order__status="Completed",
                    )
                    .values("order__order_date")
                    .annotate(total_quantity=Sum("quantity"))
                    .order_by("order__order_date")
                )

                all_sales_data = {}
                for entry in customer_sales_data:
                    date = entry["order__order_date"]
                    all_sales_data[date] = all_sales_data.get(date, 0) + (entry["total_quantity"] or 0)
                for entry in manual_sales_data:
                    date = entry["order__order_date"]
                    all_sales_data[date] = all_sales_data.get(date, 0) + (entry["total_quantity"] or 0)

                # ------------------------------------------------------------------
                # 2. Forecast next month's demand
                # ------------------------------------------------------------------
                if all_sales_data:
                    df = pd.DataFrame([
                        {"order__order_date": date, "total_quantity": qty}
                        for date, qty in all_sales_data.items()
                    ])
                    df["order__order_date"] = (
                        pd.to_datetime(df["order__order_date"])
                        .dt.tz_localize(None)
                        .dt.to_period("M")
                    )
                    df = df.groupby("order__order_date").sum().reset_index()

                    if len(df) < 2:
                        # Not enough data — fallback to conservative estimate
                        forecasted_qty = 10
                        has_sales_data = False
                        no_sales_count += 1
                    else:
                        df["time_index"] = np.arange(len(df))
                        X = df[["time_index"]].values
                        y = df["total_quantity"].values

                        model = LinearRegression()
                        model.fit(X, y)
                        forecast = model.predict(np.array([[len(df)]]))

                        forecasted_qty = max(0, round(forecast[0]))
                        has_sales_data = True
                else:
                    # No sales data at all
                    forecasted_qty = 0
                    has_sales_data = False
                    no_sales_count += 1

                # ------------------------------------------------------------------
                # 3. Compare stock vs forecast
                # ------------------------------------------------------------------
                current_stock = product.stock_quantity
                restock_needed = current_stock < forecasted_qty
                if restock_needed:
                    restock_needed_count += 1

                # ------------------------------------------------------------------
                # 4. Update or create DemandCheckLog entry
                # ------------------------------------------------------------------
                recent_log = DemandCheckLog.objects.filter(
                    product=product,
                    is_deleted=False,
                    checked_at__gte=django_timezone.now() - timedelta(hours=24),
                ).first()

                if recent_log:
                    recent_log.forecasted_quantity = forecasted_qty
                    recent_log.current_stock = current_stock
                    recent_log.restock_needed = restock_needed
                    recent_log.checked_at = django_timezone.now()
                    recent_log.save()
                else:
                    DemandCheckLog.objects.filter(
                        product=product, is_deleted=False
                    ).update(is_deleted=True, deleted_at=django_timezone.now())

                    DemandCheckLog.objects.create(
                        product=product,
                        forecasted_quantity=forecasted_qty,
                        current_stock=current_stock,
                        restock_needed=restock_needed,
                    )

                updated_count += 1
                results.append({
                    "product_id": product.product_id,
                    "product_name": product.name,
                    "forecasted_quantity": int(forecasted_qty),
                    "current_stock": int(current_stock),
                    "restock_needed": restock_needed,
                    "has_sales_data": has_sales_data,
                })

            except Exception as e:
                error_count += 1
                print(f"Error forecasting {product.name}: {str(e)}")
                continue

    # ------------------------------------------------------------------
    # 5. Build and return summary response
    # ------------------------------------------------------------------
    message_parts = [f"Forecast updated for {updated_count} products."]
    if restock_needed_count > 0:
        message_parts.append(f"{restock_needed_count} products need restocking.")
    if no_sales_count > 0:
        message_parts.append(f"{no_sales_count} products using default forecasts (no sales data).")
    if error_count > 0:
        message_parts.append(f"{error_count} products had errors.")

    return JsonResponse({
        "success": True,
        "message": " ".join(message_parts),
        "updated_count": updated_count,
        "restock_needed_count": restock_needed_count,
        "no_sales_count": no_sales_count,
        "error_count": error_count,
        "total_products": products.count(),
        "results": results[:20],
    })

@csrf_exempt
def single_product_forecast_api(request):
    """
    Single product forecast API - Returns forecast data for a specific product (for charts).
    This is for viewing forecast details for one product.
    """
    from .utils.forecasting import get_forecast_with_accuracy, get_sales_timeseries
    
    # Get product name or product_id from query parameters or POST data
    if request.method == "GET":
        product_name = request.GET.get("product")
        product_id = request.GET.get("product_id")
    else:  # POST
        data = json.loads(request.body) if request.body else {}
        product_name = data.get("product")
        product_id = data.get("product_id")

    if not product_name and not product_id:
        return JsonResponse({"error": "Product name or product_id is required"}, status=400)

    try:
        if product_id:
            product = Product.objects.get(product_id=product_id, is_deleted=False)
        else:
            product = Product.objects.filter(name__icontains=product_name, is_deleted=False).first()
            if not product:
                return JsonResponse({"error": f"Product '{product_name}' not found"}, status=404)
    except Product.DoesNotExist:
        return JsonResponse({"error": f"Product not found"}, status=404)

    # Get forecast using Linear Regression (30 days ahead, daily frequency)
    result = get_forecast_with_accuracy(
        product_id=product.product_id,
        steps=30,
        freq="D"  # Daily frequency
    )
    
    if "error" in result:
        return JsonResponse({"error": result["error"]}, status=404)
    
    # Get actual sales history (last 30 days)
    ts = get_sales_timeseries(product.product_id, freq="D")
    
    actual_sales_data = []
    if ts is not None and len(ts) > 0:
        # Get last 30 days of actual sales
        recent_ts = ts.tail(30)
        actual_sales_data = [
            {"label": date.strftime("%Y-%m-%d"), "value": int(val)}
            for date, val in recent_ts.items()
        ]
    
    # Prepare forecast data
    forecast_sales_data = [
        {"label": date, "value": int(val)}
        for date, val in zip(result["forecast_dates"], result["forecast_values"])
    ]
    
    # Get monthly forecast for restock recommendation
    from .utils.forecasting import get_monthly_forecast_for_reorder
    monthly_forecast, _ = get_monthly_forecast_for_reorder(product.product_id)
    
    current_stock = product.stock_quantity
    restock_needed = monthly_forecast is not None and current_stock < monthly_forecast
    
    # Prepare response
    response_data = {
        "actual": actual_sales_data,
        "forecast": forecast_sales_data,
        "product_id": product.product_id,
        "product_name": product.name,
        "restock_needed": bool(restock_needed),
        "forecasted_quantity": int(monthly_forecast) if monthly_forecast else None,
        "current_stock": int(current_stock),
        "chart_type": "bar",
        "method_used": result["method"],
    }
    
    # Include accuracy metrics if available
    if result.get("metrics"):
        response_data["accuracy_metrics"] = {
            "mae": round(result["metrics"]["mae"], 2),
            "rmse": round(result["metrics"]["rmse"], 2),
            "mape": round(result["metrics"]["mape"], 2) if result["metrics"]["mape"] else None,
            "accuracy_percent": round(result["metrics"]["accuracy_percent"], 2) if result["metrics"]["accuracy_percent"] else None,
        }
    
    return JsonResponse(response_data)

# Update the best_seller_api function around line 419
@csrf_exempt
def best_seller_api(request):
    """
    Enhanced API endpoint that returns best sellers with both quantity and revenue data
    INCLUDING MANUAL ORDERS
    """
    try:
        from apps.orders.models import OrderItem, ManualOrderItem

        # Customer order best sellers
        customer_best_sellers = (
            OrderItem.objects.filter(order__is_deleted=False, order__status="Completed")
            .values("product_variant__product__name")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(
                    ExpressionWrapper(
                        F("quantity") * F("price_at_order"), output_field=DecimalField()
                    )
                ),
                product_name=F("product_variant__product__name"),
            )
            .order_by("-total_quantity")
        )

        # Manual order best sellers
        manual_best_sellers = (
            ManualOrderItem.objects.filter(
                order__is_deleted=False, order__status="Completed"
            )
            .values("product_variant__product__name")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(
                    ExpressionWrapper(
                        F("quantity") * F("price_at_order"), output_field=DecimalField()
                    )
                ),
                product_name=F("product_variant__product__name"),
            )
            .order_by("-total_quantity")
        )

        # Combine both datasets
        combined_sellers = {}

        for item in customer_best_sellers:
            name = item["product_name"]
            if name not in combined_sellers:
                combined_sellers[name] = {
                    "total_quantity": 0,
                    "total_revenue": Decimal("0.00"),
                }
            combined_sellers[name]["total_quantity"] += item["total_quantity"] or 0
            combined_sellers[name]["total_revenue"] += item["total_revenue"] or Decimal(
                "0.00"
            )

        for item in manual_best_sellers:
            name = item["product_name"]
            if name not in combined_sellers:
                combined_sellers[name] = {
                    "total_quantity": 0,
                    "total_revenue": Decimal("0.00"),
                }
            combined_sellers[name]["total_quantity"] += item["total_quantity"] or 0
            combined_sellers[name]["total_revenue"] += item["total_revenue"] or Decimal(
                "0.00"
            )

        # Sort by total quantity and take top 5
        best_sellers_list = []
        sorted_sellers = sorted(
            combined_sellers.items(), key=lambda x: x[1]["total_quantity"], reverse=True
        )[:5]

        for name, data in sorted_sellers:
            best_sellers_list.append(
                {
                    "product_name": name,
                    "total_quantity": int(data["total_quantity"]),
                    "total_revenue": float(data["total_revenue"]),
                }
            )

        return JsonResponse(best_sellers_list, safe=False)

    except Exception as e:
        print(f"Error in best_seller_api: {e}")
        # Return dummy data if there's an error
        dummy_data = [
            {
                "product_name": "Product A",
                "total_quantity": 150,
                "total_revenue": 1500.00,
            },
            {
                "product_name": "Product B",
                "total_quantity": 120,
                "total_revenue": 2400.00,
            },
            {
                "product_name": "Product C",
                "total_quantity": 100,
                "total_revenue": 1000.00,
            },
            {
                "product_name": "Product D",
                "total_quantity": 80,
                "total_revenue": 1600.00,
            },
            {
                "product_name": "Product E",
                "total_quantity": 75,
                "total_revenue": 750.00,
            },
        ]
        return JsonResponse(dummy_data, safe=False)

from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json

from .models import DemandCheckLog


# ================================
# API: Restock Notifications
# ================================
@login_required
def restock_notifications_api(request):
    """
    Returns products that need restocking based on DemandCheckLog.
    Only shows products where current_stock < forecasted_quantity.
    Optional filter by product name using GET param `product`.
    """
    product_name = request.GET.get("product")

    logs = DemandCheckLog.objects.filter(
        restock_needed=True,
        is_deleted=False
    ).select_related('product', 'product__supplier_profile').order_by('-checked_at')

    if product_name:
        logs = logs.filter(product__name__icontains=product_name)

    data = []
    for log in logs:
        # Get the latest stock from the Product model (not the stored value)
        current_stock = log.product.stock_quantity
        # Only include if current stock is below forecast
        if current_stock < log.forecasted_quantity:
            # Calculate quantity needed
            quantity_needed = max(0, log.forecasted_quantity - current_stock)
            
            data.append({
                "id": log.id,
                "product_id": log.product.product_id,
                "product_name": log.product.name,
                "forecasted_quantity": int(log.forecasted_quantity),
                "current_stock": int(current_stock),
                "quantity_needed": int(quantity_needed),
                "restock_needed": True,
                "supplier_name": log.product.supplier_profile.company_name if log.product.supplier_profile else "None",
                "checked_at": log.checked_at.strftime("%Y-%m-%d %H:%M:%S"),
            })

    return JsonResponse(data, safe=False)


# ================================
# View: Restock Notifications Page
# ================================
@login_required
def restock_notifications_view(request):
    """
    View page showing products that need restocking.
    Only shows products where current_stock < forecasted_quantity.
    """
    logs = DemandCheckLog.objects.filter(
        restock_needed=True,
        is_deleted=False
    ).select_related('product', 'product__supplier_profile').order_by("-checked_at")

    # Filter to ensure only actual restock-needed items
    # Use the actual current stock from Product model, not the stored value
    active_logs = []
    for log in logs:
        # Get the latest stock from the Product model
        current_stock = log.product.stock_quantity
        # Only include if current stock is less than forecasted quantity
        if current_stock < log.forecasted_quantity:
            # Update the log's current_stock to reflect the latest value
            log.current_stock = current_stock
            # Calculate quantity needed
            log.quantity_needed = max(0, log.forecasted_quantity - current_stock)
            active_logs.append(log)

    context = {
        "logs": active_logs,
        "page_title": "Restock Notifications"
    }
    return render(request, "inventory/notification/notification_list.html", context)


# ================================
# Soft-delete notifications
# ================================
@csrf_exempt
@login_required
def deleted_notifications(request):
    if request.method == "POST":
        data = json.loads(request.body)
        ids = data.get("ids", [])

        if ids:
            logs = DemandCheckLog.objects.filter(id__in=ids, is_deleted=False)
            for log in logs:
                log.delete()  # Soft delete
            return JsonResponse({"status": "success", "deleted_count": len(ids)})
        return JsonResponse({"status": "no ids provided"}, status=400)

    return JsonResponse({"status": "invalid method"}, status=405)


# ================================
# Restore soft-deleted notifications
# ================================
@csrf_exempt
@login_required
def restore_notifications(request):
    if request.method == "POST":
        data = json.loads(request.body)
        ids = data.get("ids", [])

        if ids:
            logs = DemandCheckLog.objects.filter(id__in=ids, is_deleted=True)
            for log in logs:
                log.restore()
            return JsonResponse({"status": "success", "restored_count": len(ids)})
        return JsonResponse({"status": "no ids provided"}, status=400)

    return JsonResponse({"status": "invalid method"}, status=405)


# ================================
# Deleted Notifications View
# ================================
@login_required
def deleted_notifications_view(request):
    logs = DemandCheckLog.objects.filter(is_deleted=True).order_by("-deleted_at")
    context = {"logs": logs}
    return render(request, "inventory/notification/deleted_notifications.html", context)


# ================================
# Auto-dismiss resolved notifications
# ================================
def auto_dismiss_resolved_notifications():
    """
    Automatically soft-delete notifications that are no longer needed.
    A notification is resolved when current_stock >= forecasted_quantity.
    """
    dismissed_count = 0
    logs = DemandCheckLog.objects.filter(restock_needed=True, is_deleted=False)

    for log in logs:
        # Refresh product stock quantity
        current_stock = log.product.stock_quantity

        # Update log with current stock
        log.current_stock = current_stock

        if current_stock >= log.forecasted_quantity:
            # Resolved - soft delete
            log.restock_needed = False
            log.delete()
            dismissed_count += 1
        else:
            # Still needs restocking - keep flag true
            log.restock_needed = True
            log.save(update_fields=["restock_needed", "current_stock"])

    return dismissed_count


from django.views.decorators.http import require_GET
from django.http import JsonResponse
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from decimal import Decimal
from apps.inventory.models import Product, DemandCheckLog

@require_GET
def product_details_api(request, product_id):
    """
    API endpoint to get detailed product information for the modal.
    Includes latest forecast data instead of reorder level.
    """
    try:
        product = Product.objects.select_related("supplier_profile", "category").get(
            id=product_id, is_deleted=False
        )

        from apps.store.models import ProductVariant
        from apps.orders.models import OrderItem, ManualOrderItem

        variant = ProductVariant.objects.filter(product=product).first()

        # ------------------------------------------------------------------
        # SALES DATA (Customer + Manual)
        # ------------------------------------------------------------------
        customer_sales_data = OrderItem.objects.filter(
            product_variant__product=product,
            order__is_deleted=False,
            order__status="Completed",
        ).aggregate(
            total_quantity=Sum("quantity"),
            total_revenue=Sum(
                ExpressionWrapper(
                    F("quantity") * F("price_at_order"), output_field=DecimalField()
                )
            ),
        )

        manual_sales_data = ManualOrderItem.objects.filter(
            product_variant__product=product,
            order__is_deleted=False,
            order__status="Completed",
        ).aggregate(
            total_quantity=Sum("quantity"),
            total_revenue=Sum(
                ExpressionWrapper(
                    F("quantity") * F("price_at_order"), output_field=DecimalField()
                )
            ),
        )

        total_sales_quantity = (customer_sales_data["total_quantity"] or 0) + (
            manual_sales_data["total_quantity"] or 0
        )
        total_sales_revenue = (
            customer_sales_data["total_revenue"] or Decimal("0.00")
        ) + (manual_sales_data["total_revenue"] or Decimal("0.00"))

        # ------------------------------------------------------------------
        # FORECAST DATA (Replace reorder_level)
        # ------------------------------------------------------------------
        latest_forecast = (
            DemandCheckLog.objects.filter(product=product, is_deleted=False)
            .order_by("-checked_at")
            .first()
        )

        # ------------------------------------------------------------------
        # SUPPLIER INFO (Safe access)
        # ------------------------------------------------------------------
        supplier = product.supplier_profile
        supplier_data = (
            {
                "id": supplier.id,
                "name": supplier.company_name,
                "phone": supplier.phone,
                "email": supplier.user.email if supplier and supplier.user else None,
                "address": supplier.address,
            }
            if supplier
            else None
        )

        # ------------------------------------------------------------------
        # RESPONSE PAYLOAD
        # ------------------------------------------------------------------
        response_data = {
            "id": product.id,
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "price": float(product.price),
            "cost_price": float(product.cost_price),
            "last_purchase_price": (
                float(product.last_purchase_price)
                if product.last_purchase_price
                else None
            ),
            "stock_quantity": product.stock_quantity,
            "image": product.image.url if product.image else None,
            "forecasted_quantity": (
                latest_forecast.forecasted_quantity if latest_forecast else None
            ),
            "restock_needed": (
                latest_forecast.restock_needed if latest_forecast else None
            ),
            "unit": product.unit,
            "total_sales": int(total_sales_quantity),
            "total_revenue": float(total_sales_revenue),
            "is_active": product.is_active,
            "created_at": product.created_at.isoformat(),
            "updated_at": product.updated_at.isoformat(),
            "supplier": supplier_data,
            "category": (
                {
                    "id": product.category.id if product.category else None,
                    "name": product.category.name if product.category else None,
                }
                if product.category
                else None
            ),
        }

        if variant:
            response_data["variant"] = {
                "id": variant.id,
                "sku": variant.sku or "",
                "size": variant.size or "",
                "color": variant.color or "",
                "price": (
                    float(variant.price) if variant.price else float(product.price)
                ),
                "is_active": variant.is_active,
            }

        return JsonResponse(response_data)

    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)
    except Exception as e:
        import traceback

        print(f"ERROR in product_details_api: {e}")
        traceback.print_exc()
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)

@require_GET
def demand_forecast(request):
    try:
        product_name = request.GET.get("product_name")
        if not product_name:
            return JsonResponse(
                {"error": "product_name parameter is required"}, status=400
            )

        # Adjustable windows, default 6 past and 6 future months
        try:
            past_months = int(request.GET.get("past_months", 6))
            future_months = int(request.GET.get("future_months", 6))
        except ValueError:
            return JsonResponse(
                {"error": "past_months and future_months must be integers"}, status=400
            )

        past_months = max(0, min(past_months, 36))
        future_months = max(1, min(future_months, 36))

        # INCLUDE MANUAL ORDERS in sales data
        from apps.orders.models import OrderItem, ManualOrderItem

        # Customer order sales
        customer_sales_data = (
            OrderItem.objects.filter(
                product_variant__product__name=product_name,
                order__is_deleted=False,
                order__status="Completed",
            )
            .values("order__order_date")
            .annotate(total_quantity=Sum("quantity"))
            .order_by("order__order_date")
        )

        # Manual order sales
        manual_sales_data = (
            ManualOrderItem.objects.filter(
                product_variant__product__name=product_name,
                order__is_deleted=False,
                order__status="Completed",
            )
            .values("order__order_date")
            .annotate(total_quantity=Sum("quantity"))
            .order_by("order__order_date")
        )

        # Combine both datasets
        all_sales_data = {}

        for entry in customer_sales_data:
            date = entry["order__order_date"]
            if date not in all_sales_data:
                all_sales_data[date] = 0
            all_sales_data[date] += entry["total_quantity"] or 0

        for entry in manual_sales_data:
            date = entry["order__order_date"]
            if date not in all_sales_data:
                all_sales_data[date] = 0
            all_sales_data[date] += entry["total_quantity"] or 0

        if not all_sales_data:
            return JsonResponse({"error": "No sales data found"}, status=404)

        # Convert to DataFrame
        df_data = [
            {"order__order_date": date, "total_quantity": qty}
            for date, qty in all_sales_data.items()
        ]
        df = pd.DataFrame(df_data)
        df["order__order_date"] = (
            pd.to_datetime(df["order__order_date"])
            .dt.tz_localize(None)
            .dt.to_period("M")
        )
        df = df.groupby("order__order_date").sum().reset_index()

        # Add time index for regression
        df["time_index"] = np.arange(len(df))

        X = df[["time_index"]].values
        y = df["total_quantity"].values

        model = LinearRegression()
        model.fit(X, y)

        # Forecast adjustable number of future months
        future_index = np.arange(len(df), len(df) + future_months).reshape(-1, 1)
        forecast = model.predict(future_index)

        forecast_data = []
        last_date = df["order__order_date"].iloc[-1].to_timestamp()

        # FIXED: Generate consecutive months properly
        for i, qty in enumerate(forecast):
            # Use proper month increment to avoid skipping months
            forecast_month = last_date + pd.DateOffset(months=i + 1)
            forecast_data.append(
                {"label": forecast_month.strftime("%Y-%m"), "value": max(0, round(qty))}
            )

        # Prepare actual sales data for chart (limit to last N past months)
        actual_data_full = [
            {
                "label": row["order__order_date"].strftime("%Y-%m"),
                "value": int(row["total_quantity"]),
            }
            for _, row in df.iterrows()
        ]
        actual_data = actual_data_full[-past_months:] if past_months > 0 else []

        # Calculate total forecasted quantity across the horizon
        total_forecasted_qty = sum(item["value"] for item in forecast_data)

        # Source of truth for current stock: Product.stock_quantity
        product_obj = Product.objects.filter(name=product_name).first()
        if product_obj:
            current_stock = int(product_obj.stock_quantity)
        else:
            # Fallback: derive from StockMovement if product name not found
            stock_agg = StockMovement.objects.filter(
                product__name=product_name
            ).aggregate(
                stock_in=Sum("quantity", filter=Q(movement_type="IN")),
                stock_out=Sum("quantity", filter=Q(movement_type="OUT")),
            )
            current_stock = int(
                (stock_agg["stock_in"] or 0) - (stock_agg["stock_out"] or 0)
            )

        restock_needed = total_forecasted_qty > current_stock

        return JsonResponse(
            {
                "product_name": product_name,
                "current_stock": current_stock,
                "forecasted_quantity": total_forecasted_qty,
                "restock_needed": restock_needed,
                "actual": actual_data,
                "forecast": forecast_data,
                "params": {
                    "past_months": past_months,
                    "future_months": future_months,
                },
                "chart_type": "bar",  # ADD THIS for bar chart support
            }
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
from django.db.models import Sum, F, Q
from django.http import JsonResponse
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime
from apps.orders.models import OrderItem, ManualOrderItem


def sales_forecast(request):
    """
    Returns total monthly sales (revenue) history + linear regression forecast.
    Example URL:
      /inventory/api/sales_forecast/?past_months=6&future_months=6
    """

    try:
        # Get adjustable window
        try:
            past_months = int(request.GET.get("past_months", 6))
            future_months = int(request.GET.get("future_months", 6))
        except ValueError:
            return JsonResponse(
                {"error": "past_months and future_months must be integers"}, status=400
            )

        past_months = max(0, min(past_months, 36))
        future_months = max(1, min(future_months, 36))

        # Combine customer + manual sales (revenue-based)
        customer_sales = (
            OrderItem.objects.filter(
                order__status="Completed",
                order__is_deleted=False,
            )
            .annotate(total_value=F("quantity") * F("price_at_order"))
            .values("order__order_date")
            .annotate(monthly_sales=Sum("total_value"))
        )

        manual_sales = (
            ManualOrderItem.objects.filter(
                order__status="Completed",
                order__is_deleted=False,
            )
            .annotate(total_value=F("quantity") * F("price_at_order"))
            .values("order__order_date")
            .annotate(monthly_sales=Sum("total_value"))
        )

        # Combine both into one dict keyed by date
        all_sales = {}
        for entry in customer_sales:
            date = entry["order__order_date"]
            all_sales[date] = all_sales.get(date, 0) + entry["monthly_sales"]
        for entry in manual_sales:
            date = entry["order__order_date"]
            all_sales[date] = all_sales.get(date, 0) + entry["monthly_sales"]

        if not all_sales:
            return JsonResponse({"error": "No sales data found"}, status=404)

        # Convert to DataFrame
        df_data = [
            {"order_date": date, "sales": value} for date, value in all_sales.items()
        ]
        df = pd.DataFrame(df_data)
        df["order_date"] = pd.to_datetime(df["order_date"]).dt.to_period("M")
        df = df.groupby("order_date").sum().reset_index()

        # Prepare regression
        df["time_index"] = np.arange(len(df))
        X = df[["time_index"]].values
        y = df["sales"].values
        model = LinearRegression()
        model.fit(X, y)

        # Forecast next N months
        future_index = np.arange(len(df), len(df) + future_months).reshape(-1, 1)
        forecast = model.predict(future_index)

        # Build response lists
        last_date = df["order_date"].iloc[-1].to_timestamp()
        forecast_data = []
        for i, val in enumerate(forecast):
            forecast_month = last_date + pd.DateOffset(months=i + 1)
            forecast_data.append(
                {"label": forecast_month.strftime("%Y-%m"), "value": round(float(val), 2)}
            )

        actual_data_full = [
            {"label": row["order_date"].strftime("%Y-%m"), "value": round(float(row["sales"]), 2)}
            for _, row in df.iterrows()
        ]
        actual_data = actual_data_full[-past_months:] if past_months > 0 else []

        return JsonResponse(
            {
                "actual": actual_data,
                "forecast": forecast_data,
                "params": {"past_months": past_months, "future_months": future_months},
            }
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
from django.db.models import Sum
from django.http import JsonResponse
from django.db.models.functions import TruncMonth
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from apps.orders.models import OrderItem, ManualOrderItem

def market_trend_analysis(request):
    try:
        year = int(request.GET.get("year", pd.Timestamp.now().year))

        from django.db.models import F, ExpressionWrapper, DecimalField
        from django.db.models.functions import TruncMonth

        # Total = price_at_order * quantity
        sales_value = ExpressionWrapper(
            F("price_at_order") * F("quantity"),
            output_field=DecimalField(max_digits=15, decimal_places=2)
        )

        # Customer Orders - get category objects to resolve root
        customer_sales = (
            OrderItem.objects.filter(
                order__status="Completed",
                order__is_deleted=False,
                order__order_date__year=year,
            )
            .select_related('product_variant__product__category')
            .annotate(month=TruncMonth("order__order_date"))
            .values("month", "product_variant__product__category")
            .annotate(total_sales=Sum(sales_value))
        )

        # Manual Orders - get category objects to resolve root
        manual_sales = (
            ManualOrderItem.objects.filter(
                order__status="Completed",
                order__is_deleted=False,
                order__order_date__year=year,
            )
            .select_related('product_variant__product__category')
            .annotate(month=TruncMonth("order__order_date"))
            .values("month", "product_variant__product__category")
            .annotate(total_sales=Sum(sales_value))
        )

        # Build a category ID to root name mapping
        category_ids = set()
        for entry in list(customer_sales) + list(manual_sales):
            cat_id = entry.get("product_variant__product__category")
            if cat_id:
                category_ids.add(cat_id)
        
        # Fetch all categories and map to root names
        categories = Category.objects.filter(id__in=category_ids)
        cat_to_root = {}
        for cat in categories:
            root = cat.get_root()
            cat_to_root[cat.id] = root.name if root else cat.name

        # Aggregate by ROOT category name
        all_data = {}
        for entry in list(customer_sales) + list(manual_sales):
            month = entry["month"].strftime("%Y-%m")
            cat_id = entry["product_variant__product__category"]
            
            # Get root category name
            if cat_id and cat_id in cat_to_root:
                category = cat_to_root[cat_id]
            else:
                category = "Uncategorized"
            
            total = float(entry["total_sales"] or 0)
            all_data.setdefault(month, {}).setdefault(category, 0)
            all_data[month][category] += total

        if not all_data:
            return JsonResponse({"error": "No sales data found"}, status=404)

        # Convert to DataFrame
        df = pd.DataFrame(all_data).T.fillna(0)
        df.index.name = "month"
        df = df.reset_index()

        # Calculate total market
        df["total_market"] = df.drop(columns=["month"]).sum(axis=1)

        # Regression trend line
        X = np.arange(len(df)).reshape(-1, 1)
        y = df["total_market"].values
        model = LinearRegression()
        model.fit(X, y)
        forecast = model.predict(X)

        trend_data = [
            {"month": df["month"].iloc[i], "value": round(float(forecast[i]), 2)}
            for i in range(len(df))
        ]

        return JsonResponse(
            {
                "year": year,
                "categories": [col for col in df.columns if col not in ["month", "total_market"]],
                "data": [
                    {"month": row["month"], **{col: float(row[col]) for col in df.columns if col not in ["month", "total_market"]}}
                    for _, row in df.iterrows()
                ],
                "trend": trend_data,
            }
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

from django.db.models import Q
from apps.inventory.models import StockMovement, Product


# Update the get_sales_and_stock_analytics_by_name function around line 853
def get_sales_and_stock_analytics_by_name():
    from apps.orders.models import OrderItem

    # Total sales quantity per product (INCLUDING MANUAL ORDERS)
    customer_sales_per_product = (
        OrderItem.objects.values(product_name=F("product_variant__product__name"))
        .annotate(total_quantity_sold=Sum("quantity"))
        .order_by("-total_quantity_sold")
    )

    manual_sales_per_product = (
        ManualOrderItem.objects.values(product_name=F("product_variant__product__name"))
        .annotate(total_quantity_sold=Sum("quantity"))
        .order_by("-total_quantity_sold")
    )

    # Total revenue per product (INCLUDING MANUAL ORDERS)
    customer_revenue_per_product = (
        OrderItem.objects.values(product_name=F("product_variant__product__name"))
        .annotate(total_revenue=Sum(F("price_at_order") * F("quantity")))
        .order_by("-total_revenue")
    )

    manual_revenue_per_product = (
        ManualOrderItem.objects.values(product_name=F("product_variant__product__name"))
        .annotate(total_revenue=Sum(F("price_at_order") * F("quantity")))
        .order_by("-total_revenue")
    )

    # Current stock per product from StockMovement (grouped by product name)
    stock_aggregation = (
        StockMovement.objects.values(product_name=F("product__name"))
        .annotate(
            stock_in=Sum("quantity", filter=Q(movement_type="IN")),
            stock_out=Sum("quantity", filter=Q(movement_type="OUT")),
        )
        .annotate(current_stock=F("stock_in") - F("stock_out"))
        .order_by("-current_stock")
    )

    # Combine sales data
    sales_dict = {}
    for item in customer_sales_per_product:
        name = item["product_name"]
        sales_dict[name] = sales_dict.get(name, 0) + item["total_quantity_sold"]

    for item in manual_sales_per_product:
        name = item["product_name"]
        sales_dict[name] = sales_dict.get(name, 0) + item["total_quantity_sold"]

    # Combine revenue data
    revenue_dict = {}
    for item in customer_revenue_per_product:
        name = item["product_name"]
        revenue_dict[name] = revenue_dict.get(name, Decimal("0.00")) + (
            item["total_revenue"] or Decimal("0.00")
        )

    for item in manual_revenue_per_product:
        name = item["product_name"]
        revenue_dict[name] = revenue_dict.get(name, Decimal("0.00")) + (
            item["total_revenue"] or Decimal("0.00")
        )

    stock_dict = {
        item["product_name"]: item["current_stock"] or 0 for item in stock_aggregation
    }

    # Get all unique product names
    all_product_names = (
        set(sales_dict.keys()) | set(revenue_dict.keys()) | set(stock_dict.keys())
    )

    # Fetch products by name for additional info if needed (optional)
    products = Product.objects.filter(name__in=all_product_names)
    product_names = {p.name for p in products}

    analytics = []
    for name in all_product_names:
        analytics.append(
            {
                "product_name": name,
                "total_quantity_sold": sales_dict.get(name, 0),
                "total_revenue": float(
                    revenue_dict.get(name, Decimal("0.00"))
                ),  # Convert Decimal to float
                "current_stock": stock_dict.get(name, 0),
            }
        )

    return analytics


def sales_stock_analytics_view(request):
    analytics = get_sales_and_stock_analytics_by_name()
    return JsonResponse({"analytics": analytics})


# ---------------------- A D M I N   K P I S   A P I ------------------------- #
from django.contrib.auth.decorators import login_required


@login_required
def admin_kpis_api(request):
    """
    Returns admin KPIs for a given date range (defaults to last 30 days):
    INCLUDING MANUAL ORDERS
    FIXED: Now properly handles order and delivery status for all time ranges
    FIXED: Prevents showing future dates with no actual data
    """
    try:
        from apps.orders.models import OrderItem, ManualOrderItem

        # Date range
        # --- DATE RANGE HANDLING ---
        tz_now = django_timezone.now()
        today = tz_now.date()

        # Optional new query parameter: range_type
        range_type = request.GET.get("range_type", "").lower()

        # Default start/end (still safe fallback)
        default_start = (tz_now - timedelta(days=29)).date()
        default_end = today

        # Adjust defaults based on range_type
        if range_type == "this_month":
            default_start = today.replace(day=1)
        elif range_type == "last_month":
            # Handle last month cleanly even across year boundaries
            first_of_this_month = today.replace(day=1)
            last_month_end = first_of_this_month - timedelta(days=1)
            default_start = last_month_end.replace(day=1)
            default_end = last_month_end
        elif range_type == "this_year":
            default_start = today.replace(month=1, day=1)
        # (otherwise keep the "last 30 days" default)


        start_str = request.GET.get("start_date")
        end_str = request.GET.get("end_date")

        try:
            start_date = (
                datetime.strptime(start_str, "%Y-%m-%d").date()
                if start_str
                else default_start
            )
            end_date = (
                datetime.strptime(end_str, "%Y-%m-%d").date()
                if end_str
                else default_end
            )
        except ValueError:
            return JsonResponse(
                {"error": "Invalid date format. Use YYYY-MM-DD."}, status=400
            )

        if start_date > end_date:
            return JsonResponse(
                {"error": "start_date must be before or equal to end_date."}, status=400
            )

        # CRITICAL FIX: Cap end_date to today to prevent showing future dates
        if end_date > today:
            end_date = today

        # Calculate date range in days
        date_range_days = (end_date - start_date).days
        
        # Determine if this is an "overall" query (very large date range)
        is_overall = date_range_days > 3650  # More than 10 years = "overall"

        start_dt = datetime.combine(
            start_date,
            datetime.min.time(),
            tzinfo=django_timezone.get_current_timezone(),
        )
        end_dt = datetime.combine(
            end_date, datetime.max.time(), tzinfo=django_timezone.get_current_timezone()
        )

        # Orders query - handle overall vs date-filtered
        if is_overall:
            customer_orders_qs = Order.objects.filter(is_deleted=False)
            manual_orders_qs = ManualOrder.objects.filter(is_deleted=False)
        else:
            customer_orders_qs = Order.objects.filter(
                is_deleted=False,
                order_date__gte=start_dt,
                order_date__lte=end_dt,
            )
            manual_orders_qs = ManualOrder.objects.filter(
                is_deleted=False,
                order_date__gte=start_dt,
                order_date__lte=end_dt,
            )

        completed_customer_orders_qs = customer_orders_qs.filter(status="Completed")
        completed_manual_orders_qs = manual_orders_qs.filter(status="Completed")

        # Revenue from completed orders
        customer_revenue = OrderItem.objects.filter(
            order__in=completed_customer_orders_qs
        ).aggregate(
            total=Sum(F("quantity") * F("price_at_order"), output_field=DecimalField())
        ).get("total") or Decimal("0.00")

        manual_revenue = ManualOrderItem.objects.filter(
            order__in=completed_manual_orders_qs
        ).aggregate(
            total=Sum(F("quantity") * F("price_at_order"), output_field=DecimalField())
        ).get("total") or Decimal("0.00")

        revenue_total = customer_revenue + manual_revenue

        orders_total = customer_orders_qs.count() + manual_orders_qs.count()
        completed_orders_count = (
            completed_customer_orders_qs.count() + completed_manual_orders_qs.count()
        )
        aov = (
            (revenue_total / completed_orders_count)
            if completed_orders_count
            else Decimal("0.00")
        )

        # Order status counts - properly aggregate ALL orders in range
        customer_status_counts_qs = (
            customer_orders_qs.values("status").annotate(count=Count("id")).order_by()
        )
        manual_status_counts_qs = (
            manual_orders_qs.values("status").annotate(count=Count("id")).order_by()
        )

        status_counts = {}
        for row in customer_status_counts_qs:
            status = row["status"]
            status_counts[status] = status_counts.get(status, 0) + row["count"]

        for row in manual_status_counts_qs:
            status = row["status"]
            status_counts[status] = status_counts.get(status, 0) + row["count"]

        # Delivery status counts - handle overall vs date-filtered
        if is_overall:
            deliveries_qs = Delivery.objects.filter(order__is_deleted=False)
        else:
            deliveries_qs = Delivery.objects.filter(
                order__is_deleted=False,
                order__order_date__gte=start_dt,
                order__order_date__lte=end_dt,
            )
        
        delivery_counts_qs = (
            deliveries_qs.values("delivery_status")
            .annotate(count=Count("id"))
            .order_by()
        )
        delivery_status_counts = {
            row["delivery_status"]: row["count"] for row in delivery_counts_qs
        }

        deliveries_total = (
            delivery_status_counts.get('out_for_delivery', 0) + 
            delivery_status_counts.get('delivered', 0)
        )

        # Calculate deltas (previous period comparison)
        prev_range_days = date_range_days if date_range_days > 0 else 30
        prev_start_date = start_date - timedelta(days=prev_range_days)
        prev_end_date = start_date - timedelta(days=1)
        
        prev_start_dt = datetime.combine(
            prev_start_date,
            datetime.min.time(),
            tzinfo=django_timezone.get_current_timezone(),
        )
        prev_end_dt = datetime.combine(
            prev_end_date,
            datetime.max.time(),
            tzinfo=django_timezone.get_current_timezone(),
        )

        # Previous period orders
        prev_customer_orders = Order.objects.filter(
            is_deleted=False,
            order_date__gte=prev_start_dt,
            order_date__lte=prev_end_dt,
        ).count()
        
        prev_manual_orders = ManualOrder.objects.filter(
            is_deleted=False,
            order_date__gte=prev_start_dt,
            order_date__lte=prev_end_dt,
        ).count()
        
        prev_orders_total = prev_customer_orders + prev_manual_orders

        # Previous period revenue
        prev_customer_revenue = OrderItem.objects.filter(
            order__is_deleted=False,
            order__status="Completed",
            order__order_date__gte=prev_start_dt,
            order__order_date__lte=prev_end_dt,
        ).aggregate(
            total=Sum(F("quantity") * F("price_at_order"), output_field=DecimalField())
        ).get("total") or Decimal("0.00")

        prev_manual_revenue = ManualOrderItem.objects.filter(
            order__is_deleted=False,
            order__status="Completed",
            order__order_date__gte=prev_start_dt,
            order__order_date__lte=prev_end_dt,
        ).aggregate(
            total=Sum(F("quantity") * F("price_at_order"), output_field=DecimalField())
        ).get("total") or Decimal("0.00")

        prev_revenue_total = prev_customer_revenue + prev_manual_revenue

        # Previous period deliveries
        prev_deliveries = Delivery.objects.filter(
            order__is_deleted=False,
            order__order_date__gte=prev_start_dt,
            order__order_date__lte=prev_end_dt,
            delivery_status__in=['out_for_delivery', 'delivered']
        ).count()

        # Calculate percentage changes
        revenue_delta_pct = None
        if prev_revenue_total > 0:
            revenue_delta_pct = round(
                ((revenue_total - prev_revenue_total) / prev_revenue_total) * 100, 1
            )

        orders_delta_pct = None
        if prev_orders_total > 0:
            orders_delta_pct = round(
                ((orders_total - prev_orders_total) / prev_orders_total) * 100, 1
            )

        deliveries_delta_pct = None
        if prev_deliveries > 0:
            deliveries_delta_pct = round(
                ((deliveries_total - prev_deliveries) / prev_deliveries) * 100, 1
            )

        # Sales trend - FIXED to only show actual data dates
        # Replace the sales trend section (around line 1137-1179) with this:

        # Replace the sales trend section (around line 1137-1179) with this:

        # Sales trend
        span_days = (end_date - start_date).days
        if span_days <= 90:
            # by day
            customer_trend_qs = (
                OrderItem.objects.filter(order__in=completed_customer_orders_qs)
                .values("order__order_date__date")
                .annotate(
                    total=Sum(
                        F("quantity") * F("price_at_order"), output_field=DecimalField()
                    )
                )
                .order_by("order__order_date__date")
            )

            manual_trend_qs = (
                ManualOrderItem.objects.filter(order__in=completed_manual_orders_qs)
                .values("order__order_date__date")
                .annotate(
                    total=Sum(
                        F("quantity") * F("price_at_order"), output_field=DecimalField()
                    )
                )
                .order_by("order__order_date__date")
            )

            # Combine daily trends
            daily_totals = {}
            for row in customer_trend_qs:
                date = row["order__order_date__date"]
                # FIXED: Only include dates that are actually in the selected range
                if start_date <= date <= end_date:
                    daily_totals[date] = daily_totals.get(date, Decimal("0.00")) + (
                        row["total"] or Decimal("0.00")
                    )

            for row in manual_trend_qs:
                date = row["order__order_date__date"]
                # FIXED: Only include dates that are actually in the selected range
                if start_date <= date <= end_date:
                    daily_totals[date] = daily_totals.get(date, Decimal("0.00")) + (
                        row["total"] or Decimal("0.00")
                    )

            labels = [date.strftime("%Y-%m-%d") for date in sorted(daily_totals.keys())]
            values = [float(daily_totals[date]) for date in sorted(daily_totals.keys())]
        else:
            # by month
            customer_trend_qs = (
                OrderItem.objects.filter(order__in=completed_customer_orders_qs)
                .annotate(month=TruncMonth("order__order_date"))
                .values("month")
                .annotate(
                    total=Sum(
                        F("quantity") * F("price_at_order"), output_field=DecimalField()
                    )
                )
                .order_by("month")
            )

            manual_trend_qs = (
                ManualOrderItem.objects.filter(order__in=completed_manual_orders_qs)
                .annotate(month=TruncMonth("order__order_date"))
                .values("month")
                .annotate(
                    total=Sum(
                        F("quantity") * F("price_at_order"), output_field=DecimalField()
                    )
                )
                .order_by("month")
            )

            # Combine monthly trends
            monthly_totals = {}
            for row in customer_trend_qs:
                month = row["month"]
                # FIXED: Only include months where the data actually falls within our date range
                # Convert month to date to compare properly
                month_date = month.date() if hasattr(month, 'date') else month
                if month_date >= start_date and month_date <= end_date:
                    monthly_totals[month] = monthly_totals.get(month, Decimal("0.00")) + (
                        row["total"] or Decimal("0.00")
                    )

            for row in manual_trend_qs:
                month = row["month"]
                # FIXED: Only include months where the data actually falls within our date range
                month_date = month.date() if hasattr(month, 'date') else month
                if month_date >= start_date and month_date <= end_date:
                    monthly_totals[month] = monthly_totals.get(month, Decimal("0.00")) + (
                        row["total"] or Decimal("0.00")
                    )

            labels = [
                month.strftime("%Y-%m") for month in sorted(monthly_totals.keys())
            ]
            values = [
                float(monthly_totals[month]) for month in sorted(monthly_totals.keys())
            ]
        # Top products by quantity (COMBINED)
        customer_top_qs = (
            OrderItem.objects.filter(order__in=completed_customer_orders_qs)
            .values("product_variant__product__name")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(
                    F("quantity") * F("price_at_order"), output_field=DecimalField()
                ),
            )
            .order_by("-total_quantity")
        )

        manual_top_qs = (
            ManualOrderItem.objects.filter(order__in=completed_manual_orders_qs)
            .values("product_variant__product__name")
            .annotate(
                total_quantity=Sum("quantity"),
                total_revenue=Sum(
                    F("quantity") * F("price_at_order"), output_field=DecimalField()
                ),
            )
            .order_by("-total_quantity")
        )

        # Combine top products
        combined_products = {}
        for row in customer_top_qs:
            name = row["product_variant__product__name"]
            if name not in combined_products:
                combined_products[name] = {
                    "total_quantity": 0,
                    "total_revenue": Decimal("0.00"),
                }
            combined_products[name]["total_quantity"] += row["total_quantity"] or 0
            combined_products[name]["total_revenue"] += row["total_revenue"] or Decimal(
                "0.00"
            )

        for row in manual_top_qs:
            name = row["product_variant__product__name"]
            if name not in combined_products:
                combined_products[name] = {
                    "total_quantity": 0,
                    "total_revenue": Decimal("0.00"),
                }
            combined_products[name]["total_quantity"] += row["total_quantity"] or 0
            combined_products[name]["total_revenue"] += row["total_revenue"] or Decimal(
                "0.00"
            )

        # Sort by quantity and take top 5
        top_products = []
        sorted_products = sorted(
            combined_products.items(),
            key=lambda x: x[1]["total_quantity"],
            reverse=True,
        )[:5]

        for name, data in sorted_products:
            top_products.append(
                {
                    "product_name": name,
                    "total_quantity": int(data["total_quantity"]),
                    "total_revenue": float(data["total_revenue"]),
                }
            )

        # Low stock list
        low_stock_qs = DemandCheckLog.objects.filter(
            restock_needed=True, 
            is_deleted=False
        ).select_related('product').order_by('-checked_at') 

        low_stock = [
            {
                "product_name": log.product.name,
                "stock_quantity": int(log.product.stock_quantity),
                "forecasted_quantity": int(log.forecasted_quantity),
            }
            for log in low_stock_qs
        ]

        return JsonResponse(
            {
                "range": {
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d"),
                    "actual_end_date": today.strftime("%Y-%m-%d"),  # Show actual data cutoff
                },
                "revenue_total": float(revenue_total),
                "orders_total": orders_total,
                "average_order_value": float(aov),
                "deliveries_total": deliveries_total,
                "revenue_delta_pct": revenue_delta_pct,
                "orders_delta_pct": orders_delta_pct,
                "deliveries_delta_pct": deliveries_delta_pct,
                "order_status_counts": status_counts,
                "delivery_status_counts": delivery_status_counts,
                "sales_trend": {
                    "labels": labels,
                    "values": values,
                },
                "top_products": top_products,
                "low_stock": low_stock,
            }
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


# ==============================================================================
# CATEGORY MANAGEMENT VIEWS
# ==============================================================================

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse
from apps.inventory.models import Category
from apps.inventory.forms import CategoryForm


# ------------------------------------------------------------------------------
# ADD CATEGORY
# ------------------------------------------------------------------------------
def category_add_view(request):
    """Create a new category."""
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()  # Save first to get the category instance
            
            # Now log the audit with the saved category
            log_audit(
                user=request.user,
                action="create",
                instance=category,
                request=request,
                changes={"name": category.name, "parent": str(category.parent) if category.parent else None}
            )

            messages.success(request, "Category added successfully.")
            return redirect("inventory:category_list")
    else:
        form = CategoryForm()

    return render(request, "inventory/inventory_list/product/category_form.html", {"form": form, "title": "Add Category"})


# ------------------------------------------------------------------------------
# EDIT CATEGORY
# ------------------------------------------------------------------------------
def category_edit_view(request, pk):
    """Edit an existing category."""
    category = get_object_or_404(Category, pk=pk)
    
    if request.method == "POST":
        # Store old values before updating
        old_name = category.name
        old_parent = category.parent
        
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            updated_category = form.save()
            
            # Log the audit with before/after values
            log_audit(
                user=request.user,
                action="update",
                instance=updated_category,
                request=request,
                changes={
                    "before": {"name": old_name, "parent": str(old_parent) if old_parent else None},
                    "after": {"name": updated_category.name, "parent": str(updated_category.parent) if updated_category.parent else None},
                }
            )

            messages.success(request, "Category updated successfully.")
            return redirect("inventory:category_list")
    else:
        form = CategoryForm(instance=category)

    return render(request, "inventory/inventory_list/product/category_form.html",
        {"form": form, "title": f"Edit Category: {category.name}"},
    )
# ------------------------------------------------------------------------------
# ARCHIVE CATEGORY (Soft Deactivate)
# ------------------------------------------------------------------------------
def category_archive_view(request, pk):
    """Soft deactivate (archive) a category."""
    category = get_object_or_404(Category, pk=pk)
    category.is_active = False
    category.save(update_fields=["is_active"])
    log_audit(
        user=request.user,
        action="update",
        instance=category,
        request=request,
        changes={"status": "archived"}
    )

    messages.info(request, f"Category '{category.name}' archived.")
    return redirect("inventory:category_list")

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import ProtectedError
from apps.inventory.models import Category, Product

@require_http_methods(["GET"])
def check_category_product_count(request, pk):
    """
    AJAX endpoint: return product count, product names, 
    descendant count, and descendant names for a category.
    """
    try:
        category = get_object_or_404(Category, pk=pk)
        
        # Check for child categories - include BOTH active and inactive
        # Because we can't delete a parent even if children are archived
        descendants = category.children.all()
        descendant_count = descendants.count()
        
        # Show which ones are active vs inactive
        active_descendants = descendants.filter(is_active=True)
        inactive_descendants = descendants.filter(is_active=False)
        
        descendant_names = []
        for desc in active_descendants:
            descendant_names.append(desc.name)
        for desc in inactive_descendants:
            descendant_names.append(f"{desc.name} (archived)")
        
        has_descendants = descendant_count > 0
        
        # Get all active products directly linked to this category
        products = Product.objects.filter(
            category=category, 
            is_deleted=False,
            is_active=True
        ).values_list('name', flat=True)
        
        product_names = list(products)
        product_count = len(product_names)
        
        return JsonResponse({
            "product_count": product_count,
            "product_names": product_names,
            "has_descendants": has_descendants,
            "descendant_count": descendant_count,
            "descendant_names": descendant_names,
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=404)


@require_http_methods(["POST"])
def category_restore_view(request, pk):
    """AJAX endpoint to restore a category."""
    try:
        category = get_object_or_404(Category, pk=pk, is_active=False)
        category.is_active = True
        category.save(update_fields=["is_active", "updated_at"])
        log_audit(
        user=request.user,
        action="update",
        instance=category,
        request=request,
        changes={"status": "restored"}
    )

        
        return JsonResponse({
            "success": True,
            "message": f"Category '{category.name}' restored successfully."
        })
    except Category.DoesNotExist:
        return JsonResponse({
            "success": False,
            "error": "Category not found or already active."
        }, status=404)
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=400)


@require_http_methods(["POST"])
def category_delete_view(request, pk):
    """AJAX endpoint to permanently delete a category."""
    try:
        category = get_object_or_404(Category, pk=pk)
        
        # Check for child categories (ALL descendants, not just active)
        descendant_count = category.children.all().count()
        if descendant_count > 0:
            return JsonResponse({
                "success": False,
                "error": f"Cannot delete '{category.name}' because it has {descendant_count} subcategory(ies). Delete or move the subcategories first."
            }, status=400)
        
        # Then check for products
        product_count = category.get_direct_products_count()
        if product_count > 0:
            return JsonResponse({
                "success": False,
                "error": f"Cannot delete '{category.name}' because it has {product_count} active product(s) linked to it."
            }, status=400)
        
        category_name = category.name
        log_audit(
            user=request.user,
            action="delete",
            instance=category,
            request=request,
            changes={"name": category_name}
        )

        category.delete()
        
        return JsonResponse({
            "success": True,
            "message": f"Category '{category_name}' deleted successfully."
        })
        
    except ProtectedError:
        return JsonResponse({
            "success": False,
            "error": "Cannot delete this category because it's still referenced by other records."
        }, status=400)
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        }, status=500)
# ```

# **Key changes:**

# 1. **Fixed the pre-check** to look for ALL descendants (active + inactive), not just active ones
# 2. **Fixed the delete endpoint** to also check ALL descendants
# 3. **Better labeling** - archived subcategories are shown as "(archived)" in the list
# 4. **Better error parsing** in the frontend to handle JSON error responses

# Now when you try to delete "Groceries" that has 5 subcategories:

# **In the modal, you'll see:**
# ```
# ℹ️ Categories with subcategories:

# - Groceries (5 subcategories):
#   - Beverages
#   - Snacks
#   - Dairy (archived)
#   - Frozen Foods
#   - Bakery (archived)
# ```

# And the delete button will be disabled with "Cannot Delete" text, preventing the deletion attempt entirely.

# The error message will now display correctly if somehow it gets past the check:
# ```
# Error deleting category: Cannot delete 'Groceries' because it has 5 subcategory(ies). Delete or move the subcategories first.
# ------------------------------------------------------------------------------
# CATEGORY LIST
# ------------------------------------------------------------------------------
# apps/inventory/views.py
from django.shortcuts import render
from .models import Category

def category_list_view(request):
    """Display all categories with their parent path."""
    categories = Category.objects.all().order_by("name")
    context = {"categories": categories}
    return render(request, "inventory/inventory_list/product/category_list.html", context)


from django.shortcuts import render
from .models import Category

def archived_category_list_view(request):
    """Display all archived (inactive) categories."""
    archived_categories = Category.objects.filter(is_active=False).order_by("name")
    context = {"categories": archived_categories}
    return render(request, "inventory/inventory_list/product/category_archive_list.html", context)
