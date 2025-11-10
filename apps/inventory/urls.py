from django.urls import path
from .views import (
    inventory_list,
    delete_products,
    dashboard,
    archive_list,
    permanently_delete_products,
    restore_products,
    restock_notifications_api,
    restock_notifications_view,
    deleted_notifications,
    restore_notifications,
    deleted_notifications_view,
    product_forecast_api,  # Batch forecast update for all products
    single_product_forecast_api,  # Single product forecast for charts
    best_seller_api,
    demand_forecast,
    sales_stock_analytics_view,
    admin_kpis_api,
    product_details_api,
    # update_product,
    # get_dashboard_stats_api,
    # get_recent_activities_api # Remove if not used
    product_list,
    product_create,
    product_update,
    product_archive_list,
    category_create_ajax,
    toggle_product_active,
    category_list_view,
    archived_category_list_view,
    category_restore_view,
    check_category_product_count,
    category_delete_view,
    category_add_view,
    category_edit_view,
    category_archive_view,
    sales_forecast,
    market_trend_analysis
)

app_name = "inventory"

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),  # Your main dashboard view
    # new CRUD
    path("products/", product_list, name="product_list"),
    path("products/add/", product_create, name="product_create"),
    path("products/edit/<int:pk>/", product_update, name="product_update"),
    path("archives/", product_archive_list, name="product_archive_list"),
    # New AJAX endpoint for category creation
    path("categories/add-ajax/", category_create_ajax, name="category_create_ajax"),
    # path("inventory-list/", inventory_list, name="inventory_list"),
    # path("archive-list/", archive_list, name="archive_list"),
    path("delete-products/", delete_products, name="delete_products"),
    path(
        "permanently-delete-products/",
        permanently_delete_products,
        name="permanently_delete_products",
    ),
    path("restore-products/", restore_products, name="restore_products"),
    # path('update/<int:product_id>/', update_product, name='update_product'),
    # Product Forecast APIs
    path("api/product-forecast/", product_forecast_api, name="product_forecast_api"),  # Batch update all products
    path("api/single-product-forecast/", single_product_forecast_api, name="single_product_forecast_api"),  # Single product for charts
    path("api/demand_forecast/", demand_forecast, name="demand_forecast"),  # Monthly forecast with adjustable windows
    path("api/admin-kpis/", admin_kpis_api, name="admin_kpis_api"),
    path(
        "sales-stock-analytics/",
        sales_stock_analytics_view,
        name="sales_stock_analytics",
    ),
    # path('api/dashboard-stats/', get_dashboard_stats_api, name='get_dashboard_stats_api'),
    # Best Sellers API
    path("api/best-sellers/", best_seller_api, name="best_seller_api"),
    # Notification Views
    path(
        "notifications/", restock_notifications_view, name="restock_notifications_view"
    ),
    path(
        "notifications/deleted/",
        deleted_notifications_view,
        name="deleted_notifications_view",
    ),
    path("notifications/delete/", deleted_notifications, name="deleted_notifications"),
    path("notifications/restore/", restore_notifications, name="restore_notifications"),
    # Restock Notifications API (if your frontend needs this specific API)
    path(
        "api/restock-notifications/",
        restock_notifications_api,
        name="restock_notifications_api",
    ),
    # Product Details API
    path(
        "api/product-details/<int:product_id>/",
        product_details_api,
        name="product_details_api",
    ),
    path('toggle-active/<str:product_id>/', toggle_product_active, name='toggle_product_active'),

    # Category Management
    path('categories/', category_list_view, name='category_list'),
    path('categories/add/', category_add_view, name='category_add'),
    path('categories/<int:pk>/edit/', category_edit_view, name='category_edit'),
    path('categories/<int:pk>/archive/', category_archive_view, name='category_archive'),
    path('categories/archived/', archived_category_list_view, name='archived_category_list_view'),
    path('categories/<int:pk>/restore/', category_restore_view, name='category_restore'),
    path('categories/<int:pk>/check-products/', check_category_product_count, name='category_check_products'),
    path('categories/<int:pk>/delete/', category_delete_view, name='category_delete'),
    path("forecast/sales/", sales_forecast, name="sales_forecast"),
    path("analytics/market-trend/", market_trend_analysis, name="market_trend_analysis"),



    
]
