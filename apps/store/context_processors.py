# ==============================================================================
# REFACTORED: apps/store/context_processors.py
# ==============================================================================

from .models import Cart


def cart_item_count(request):
    """
    Context processor to add cart item count to all templates.

    Returns:
        dict: {'cart_item_count': int}
    """
    cart = None
    item_count = 0

    try:
        if request.user.is_authenticated:
            # Get authenticated user's cart
            cart = Cart.objects.filter(user=request.user).first()
        else:
            # Get guest cart from session
            session_key = request.session.session_key

            if not session_key:
                request.session.save()
                session_key = request.session.session_key

            cart = Cart.objects.filter(
                session_key=session_key, user__isnull=True
            ).first()

        if cart:
            # Count unique items (not total quantity)
            item_count = cart.items.count()

    except Exception as e:
        # Log error in production
        print(f"Context processor error: {e}")

    return {"cart_item_count": item_count}


def merge_session_cart_with_user(request, user):
    """
    Merge an anonymous session cart into the logged-in user's cart.
    Called after successful login.

    Args:
        request: HTTP request object
        user: User object that just logged in
    """
    try:
        # Ensure session key exists
        session_key = request.session.session_key

        if not session_key:
            return  # No session cart to merge

        # Get the anonymous cart
        session_cart = Cart.objects.filter(
            session_key=session_key, user__isnull=True
        ).first()

        if not session_cart:
            return  # No session cart found

        # Get or create user's cart
        user_cart, created = Cart.objects.get_or_create(user=user)

        # Merge items from session cart to user cart
        for item in session_cart.items.all():
            # Check if this product variant already exists in user's cart
            existing_item = user_cart.items.filter(
                product_variant=item.product_variant
            ).first()

            if existing_item:
                # Add quantities together
                existing_item.quantity += item.quantity
                existing_item.save()
            else:
                # Reassign item to user's cart
                item.cart = user_cart
                item.save()

        # Delete the old session cart
        session_cart.delete()

        # Clean up session
        if "session_key" in request.session:
            del request.session["session_key"]

    except Exception as e:
        print(f"Error merging session cart: {e}")
