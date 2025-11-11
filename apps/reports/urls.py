from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    path("", views.reports_dashboard, name="reports_dashboard"),
    path("inventory/", views.inventory_report, name="inventory_report"),
    path("purchase-orders/", views.purchase_orders_report, name="purchase_orders_report"),
    path("delivery/", views.delivery_report, name="delivery_report"),
    path("audit/", views.audit_report, name="audit_report"),
]

