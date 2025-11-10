from django.urls import path
from . import views

app_name = "suppliers"

urlpatterns = [
    path("orders/", views.supplier_order_list, name="supplier_order_list"),
    path(
        "orders/view/<str:purchase_order_id>/",
        views.supplier_view_order,
        name="view_order",
    ),
    path("dashboard/", views.supplier_dashboard, name="supplier_dashboard"),
    path(
        "orders/<str:purchase_order_id>/price/",
        views.supplier_price_order,
        name="supplier_price_order",
    ),
    path(
        "orders/<str:purchase_order_id>/mark-in-transit/",
        views.supplier_mark_in_transit,
        name="supplier_mark_in_transit",
    ),
    path(
        "orders/<str:purchase_order_id>/cancel/",
        views.supplier_cancel_order,
        name="supplier_cancel_order",
    ),
]