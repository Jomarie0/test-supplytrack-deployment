from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path("", views.sales_dashboard, name="sales_dashboard"),
    path("overview/", views.sales_overview, name="sales_overview"),
    path("by-product/", views.sales_by_product, name="sales_by_product"),
    path("by-customer/", views.sales_by_customer, name="sales_by_customer"),
    path("trends/", views.sales_trends, name="sales_trends"),
]

