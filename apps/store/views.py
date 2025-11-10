# ==============================================================================
# REFACTORED: apps/store/views.py
# ==============================================================================

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from apps.inventory.models import Product, Category
from .models import Cart, CartItem, ProductVariant


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================
def get_sidebar_categories(current_slug=None):
    """
    Prepare categories for sidebar with expansion flags.
    Walks up the tree from current category to mark all ancestors as expanded.

    Args:
        current_slug: Slug of currently active category

    Returns:
        List of root categories with is_expanded and is_active flags set
    """
    parent_categories = list(Category.objects.active_roots())

    # Get the current category and collect all ancestor IDs
    ancestor_ids = set()
    current_category = None

    if current_slug:
        try:
            current_category = Category.objects.get(slug=current_slug, is_active=True)

            # Walk UP the tree to collect all ancestor IDs
            current_parent = current_category.parent
            while current_parent:
                ancestor_ids.add(current_parent.id)
                current_parent = current_parent.parent

        except Category.DoesNotExist:
            pass

    # Now mark categories for the sidebar
    for parent in parent_categories:
        # Mark as active if this is the current category
        parent.is_active_slug = parent.slug == current_slug

        # Expand if this category is in the ancestor chain OR is the current category
        parent.is_expanded = (parent.id in ancestor_ids) or parent.is_active_slug

        # Fetch and mark children (this creates the annotated children list)
        parent.annotated_children = _get_annotated_children(
            parent, current_slug, ancestor_ids
        )

    return parent_categories


def _get_annotated_children(category, current_slug, ancestor_ids):
    """
    Get children with expansion flags already set.
    This ensures the attributes persist through template recursion.

    Args:
        category: Parent category
        current_slug: The slug of the currently active category
        ancestor_ids: Set of category IDs that are ancestors of current category

    Returns:
        List of children with is_expanded and is_active_slug set
    """
    children = list(category.children.filter(is_active=True))

    for child in children:
        # Mark as active if this matches current slug
        child.is_active_slug = child.slug == current_slug

        # Expand if this category is in the ancestor chain OR is the current category
        child.is_expanded = (child.id in ancestor_ids) or child.is_active_slug

        # Recursively get annotated children
        child.annotated_children = _get_annotated_children(
            child, current_slug, ancestor_ids
        )

    return children


def get_breadcrumb_list(self):
    """
    Return list of category objects from root to current.
    Used for breadcrumb navigation.

    Returns:
        list: List of Category objects in hierarchical order
        Example: For "Coffee & Tea" returns [Groceries, Beverages, Coffee & Tea]
    """
    breadcrumbs = []
    current = self

    # Walk up the tree to collect all ancestors
    while current is not None:
        breadcrumbs.insert(0, current)  # Insert at beginning to maintain order
        current = current.parent

    return breadcrumbs


def get_or_create_cart(request):
    """
    Get or create cart for the current user or session.
    Handles merging of anonymous carts when user logs in.
    """
    if request.user.is_authenticated:
        cart, created = Cart.objects.get_or_create(user=request.user)

        # Merge anonymous cart if exists
        session_key = request.session.session_key
        if session_key:
            try:
                anon_cart = Cart.objects.get(session_key=session_key, user__isnull=True)
                if anon_cart != cart:
                    # Merge items
                    for item in anon_cart.items.all():
                        existing_item = cart.items.filter(
                            product_variant=item.product_variant
                        ).first()

                        if existing_item:
                            existing_item.quantity += item.quantity
                            existing_item.save()
                        else:
                            item.cart = cart
                            item.save()

                    anon_cart.delete()
            except Cart.DoesNotExist:
                pass
    else:
        # Guest cart
        if not request.session.session_key:
            request.session.save()

        cart, created = Cart.objects.get_or_create(
            session_key=request.session.session_key, user__isnull=True
        )

    return cart


# ==============================================================================
# PRODUCT VIEWS
# ==============================================================================

def landing_page_view(request):
    best_sellers = Product.objects.filter(is_active=True, is_deleted=False).order_by('-total_sales')[:8]
    featured_products = Product.objects.filter(is_active=True, is_deleted=False).order_by('-created_at')[:8]
    categories = Category.objects.all()

    return render(request, 'store/landing_page.html', {
        'best_sellers': best_sellers,
        'featured_products': featured_products,
        'categories': categories,
    })


def product_list_view(request):
    """Display all active products with category sidebar."""
    products = Product.objects.active().select_related("category")
    sidebar_categories = get_sidebar_categories()

    context = {
        "products": products,
        "parent_categories": sidebar_categories,
        "categories": sidebar_categories,  # Backward compatibility
        "page_title": "All Products",
    }
    return render(request, "store/store_view_v1.html", context)


def product_detail_view(request, slug):
    """Display single product details."""
    product = get_object_or_404(
        Product.objects.active().select_related("category", "supplier_profile"),
        slug=slug,
    )

    context = {
        "product": product,
        "page_title": product.name,
    }
    return render(request, "store/store_detail_view.html", context)


def category_product_list_view(request, slug):
    """Display products within a category and all its descendants."""
    # Category model doesn't have is_deleted, only is_active
    category = get_object_or_404(Category.objects.filter(is_active=True), slug=slug)

    # Get all descendant category IDs using recursive method
    category_ids = category.get_descendant_ids()

    # Product model HAS is_deleted, so keep it in the filter
    products = (
        Product.objects.filter(
            category_id__in=category_ids,
            is_active=True,
            is_deleted=False,  # ✓ Keep this - Product has is_deleted
        )
        .select_related("category")
        .order_by("name")
    )

    # Get sidebar with proper expansion
    sidebar_categories = get_sidebar_categories(current_slug=slug)

    context = {
        "category": category,
        "products": products,
        "parent_categories": sidebar_categories,
        "page_title": f"Products in {category.name}",
        "current_slug": slug,
    }
    return render(request, "store/store_view_v1.html", context)


# ==============================================================================
# CART OPERATIONS
# ==============================================================================


@transaction.atomic
def add_to_cart_view(request, product_id):
    """Add product to cart. Requires login."""
    if not request.user.is_authenticated:
        # Store intent in session
        request.session["pending_add_to_cart"] = {
            "product_id": product_id,
            "quantity": (
                int(request.POST.get("quantity", 1)) if request.method == "POST" else 1
            ),
        }
        messages.info(request, "Please log in to add items to your cart.")
        login_url = reverse("users:customer_login")
        return redirect(f"{login_url}?next={request.path}")

    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("store:product_list")

    product = get_object_or_404(
        Product, id=product_id, is_active=True, is_deleted=False
    )
    quantity = int(request.POST.get("quantity", 1))

    # Validate quantity
    if quantity <= 0:
        messages.error(request, "Quantity must be at least 1.")
        return redirect(request.META.get("HTTP_REFERER", "store:product_list"))

    if product.stock_quantity < quantity:
        messages.error(
            request,
            f"Not enough stock for {product.name}. Available: {product.stock_quantity}.",
        )
        return redirect(request.META.get("HTTP_REFERER", "store:product_list"))

    # Get or create product variant
    try:
        product_variant = ProductVariant.objects.get(product=product)
    except ProductVariant.DoesNotExist:
        product_variant = ProductVariant.objects.create(
            product=product, sku=f"{product.product_id}-DEF", price=product.price
        )
    except ProductVariant.MultipleObjectsReturned:
        product_variant = ProductVariant.objects.filter(
            product=product, is_active=True
        ).first()

        if not product_variant:
            messages.error(request, f"No active variant found for {product.name}.")
            return redirect(request.META.get("HTTP_REFERER", "store:product_list"))

    cart = get_or_create_cart(request)

    # Check existing quantity in cart
    existing_item = CartItem.objects.filter(
        cart=cart, product_variant=product_variant
    ).first()

    existing_quantity = existing_item.quantity if existing_item else 0

    # Prevent adding more than available stock
    if existing_quantity + quantity > product.stock_quantity:
        messages.error(
            request,
            f"You already have {existing_quantity} in your cart. "
            f"Only {product.stock_quantity} available.",
        )
        return redirect(request.META.get("HTTP_REFERER", "store:product_list"))

    # Add or update cart item
    if existing_item:
        existing_item.quantity += quantity
        existing_item.save()
        messages.success(
            request, f"Added {quantity} more of {product.name} to your cart."
        )
    else:
        CartItem.objects.create(
            cart=cart, product_variant=product_variant, quantity=quantity
        )
        messages.success(request, f"Added {quantity} {product.name} to your cart.")

    return redirect("store:product_list")


@login_required(login_url="users:customer_login")
def cart_view(request):
    """Display the user's shopping cart."""
    cart = get_or_create_cart(request)
    cart_total = sum(item.item_total for item in cart.items.all())

    context = {
        "cart": cart,
        "cart_total": cart_total,
        "page_title": "Your Shopping Cart",
    }
    return render(request, "store/cart.html", context)


@login_required(login_url="users:customer_login")
@transaction.atomic
def update_cart_item_view(request, item_id):
    """Update quantity for a cart item."""
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("store:cart_view")

    cart_item = get_object_or_404(CartItem, id=item_id)
    cart = get_or_create_cart(request)

    # Verify ownership
    if cart_item.cart != cart:
        messages.error(request, "You do not have permission to modify this cart item.")
        return redirect("store:cart_view")

    try:
        new_quantity = int(request.POST.get("quantity", 0))
    except ValueError:
        messages.error(request, "Invalid quantity.")
        return redirect("store:cart_view")

    if new_quantity <= 0:
        # Remove item
        product_name = cart_item.product_variant.product.name
        cart_item.delete()
        messages.success(request, f"Removed '{product_name}' from your cart.")
    else:
        # Update quantity
        product = cart_item.product_variant.product

        if new_quantity > product.stock_quantity:
            messages.error(
                request, f"Only {product.stock_quantity} {product.name} left in stock."
            )
            return redirect("store:cart_view")

        cart_item.quantity = new_quantity
        cart_item.save()
        messages.success(
            request, f"Updated '{product.name}' quantity to {new_quantity}."
        )

    return redirect("store:cart_view")


@login_required(login_url="users:customer_login")
@transaction.atomic
def remove_from_cart_view(request, item_id):
    """Remove a specific item from the cart."""
    if request.method != "POST":
        messages.error(request, "Invalid request method.")
        return redirect("store:cart_view")

    cart_item = get_object_or_404(CartItem, id=item_id)
    cart = get_or_create_cart(request)

    # Verify ownership
    if cart_item.cart != cart:
        messages.error(request, "You do not have permission to remove this item.")
        return redirect("store:cart_view")

    product_name = cart_item.product_variant.product.name
    cart_item.delete()
    messages.success(request, f"'{product_name}' has been removed from your cart.")

    return redirect("store:cart_view")


def process_pending_add_to_cart(request):
    """Process any pending add-to-cart action after login."""
    pending = request.session.get("pending_add_to_cart")

    if not pending:
        return

    try:
        product_id = pending.get("product_id")
        quantity = pending.get("quantity", 1)

        product = Product.objects.get(id=product_id, is_active=True, is_deleted=False)

        # Validate stock
        if product.stock_quantity < quantity:
            messages.warning(
                request,
                f"Only {product.stock_quantity} {product.name} available in stock.",
            )
            quantity = product.stock_quantity

        if quantity <= 0:
            messages.error(request, f"{product.name} is out of stock.")
            del request.session["pending_add_to_cart"]
            return

        # Get or create product variant
        try:
            product_variant = ProductVariant.objects.get(product=product)
        except ProductVariant.DoesNotExist:
            product_variant = ProductVariant.objects.create(
                product=product, sku=f"{product.product_id}-DEF", price=product.price
            )
        except ProductVariant.MultipleObjectsReturned:
            product_variant = ProductVariant.objects.filter(
                product=product, is_active=True
            ).first()

        if not product_variant:
            messages.error(
                request, f"Product variant not available for {product.name}."
            )
            del request.session["pending_add_to_cart"]
            return

        # Get user's cart
        cart = get_or_create_cart(request)

        # Check existing quantity
        existing_item = CartItem.objects.filter(
            cart=cart, product_variant=product_variant
        ).first()

        existing_quantity = existing_item.quantity if existing_item else 0

        # Validate total quantity
        if existing_quantity + quantity > product.stock_quantity:
            available = product.stock_quantity - existing_quantity

            if available > 0:
                quantity = available
                messages.warning(
                    request,
                    f"You already have {existing_quantity} in cart. "
                    f"Added {quantity} more (max available).",
                )
            else:
                messages.error(
                    request,
                    f"You already have the maximum available {product.name} in your cart.",
                )
                del request.session["pending_add_to_cart"]
                return

        # Add to cart
        if existing_item:
            existing_item.quantity += quantity
            existing_item.save()
        else:
            CartItem.objects.create(
                cart=cart, product_variant=product_variant, quantity=quantity
            )

        messages.success(request, f"Added {quantity} {product.name} to your cart!")

    except Product.DoesNotExist:
        messages.error(request, "The product you tried to add is no longer available.")
    except Exception as e:
        messages.error(request, f"Error adding product to cart: {str(e)}")
    finally:
        # Always clear the pending action
        if "pending_add_to_cart" in request.session:
            del request.session["pending_add_to_cart"]
