# apps/store/urls.py

from django.urls import path
from . import views

app_name = "store"

urlpatterns = [
    path("", views.landing_page_view, name="landing_page"),
    path("product_list", views.product_list_view, name="product_list"),
    path("product/<slug:slug>/", views.product_detail_view, name="product_detail"),
    path(
        "category/<slug:slug>/",
        views.category_product_list_view,
        name="category_products",
    ),
    path("add-to-cart/<int:product_id>/", views.add_to_cart_view, name="add_to_cart"),
    # NEW CART URLs:
    path("cart/", views.cart_view, name="cart_view"),
    path(
        "cart/update/<int:item_id>/",
        views.update_cart_item_view,
        name="update_cart_item",
    ),
    path(
        "cart/remove/<int:item_id>/",
        views.remove_from_cart_view,
        name="remove_from_cart",
    ),
]
