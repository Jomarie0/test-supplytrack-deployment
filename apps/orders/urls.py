# apps/orders/urls.py
from django.urls import path
from .views import (
    delete_orders,
    # archived_orders,
    permanently_delete_orders,
    restore_orders,
    checkout_view,
    order_confirmation_view,
    my_orders_view,
    update_order_status,
    customer_orders_management,
    order_details_api,
    billing_dashboard,
    # manual_order_details_api,
    billing_order_detail,
    billing_manual_order_detail,
    customer_invoice_view,
    customer_invoice_pdf,
    delivery_calendar_view,
    calendar_events_api,
    

)

from .manual_views import (
    manual_order_management,
    create_manual_order,
    manual_order_details_api,
    update_manual_order,
    delete_manual_orders,
)

app_name = "orders"

urlpatterns = [
    # Regular order paths
    path("delete/", delete_orders, name="delete_orders"),
    # path('archive/', archived_orders, name='archived_orders'),
    path(
        "permanent-delete/", permanently_delete_orders, name="permanent_delete_orders"
    ),
    path("restore/", restore_orders, name="restore_orders"),
    path("checkout/", checkout_view, name="checkout"),
    path(
        "confirmation/<int:order_id>/",
        order_confirmation_view,
        name="order_confirmation",
    ),
    path("my-orders/", my_orders_view, name="my_orders"),
    path(
        "orders/update-status/<int:order_id>/",
        update_order_status,
        name="update_order_status",
    ),
    path(
        "customer-orders/",
        customer_orders_management,
        name="customer_orders_management",
    ),
    # path('api/order-details/<int:order_id>/', order_details_api, name='order_details_api'),
    path("api/details/<int:order_id>/", order_details_api, name="order_details_api"),
    # Add the missing API endpoint for status updates
    path(
        "api/update-status/<int:order_id>/",
        update_order_status,
        name="api_update_order_status",
    ),
    # Manual Order Management (Staff) - NEW ROUTES
    path("manual/", manual_order_management, name="manual_order_management"),
    path("manual/create/", create_manual_order, name="create_manual_order"),
    path(
        "manual/api/details/<int:order_id>/",
        manual_order_details_api,
        name="manual_order_details_api",
    ),
    path(
        "manual/api/update/<int:order_id>/",
        update_manual_order,
        name="update_manual_order",
    ),
    path("manual/api/delete/", delete_manual_orders, name="delete_manual_orders"),
    # billing dashboard
    path("billing/", billing_dashboard, name="billing_dashboard"),
    path("billing/order/<int:order_id>/", billing_order_detail, name="billing_order_detail"),
    path("billing/manual/<int:order_id>/", billing_manual_order_detail, name="billing_manual_order_detail"),
   # urls.py
   path('invoice/<int:order_id>/<str:order_type>/', customer_invoice_view, name='customer_invoice_view'),
    
    path('invoice/<int:order_id>/<str:order_type>/pdf/', customer_invoice_pdf, name='customer_invoice_pdf'),
    
    # Calendar routes
    path('calendar/', delivery_calendar_view, name='delivery_calendar'),
    path('api/calendar-events/', calendar_events_api, name='calendar_events_api'),
   


    
    
]
