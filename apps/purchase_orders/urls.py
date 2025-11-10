from django.urls import path
from . import views

app_name = "PO"

urlpatterns = [
    # ====== Static and specific routes FIRST ======
    path("purchase-order-list/", views.purchase_order_list, name="purchase_order_list"),
    path("delete/", views.delete_purchase_orders, name="delete_purchase_orders"),
    path("archive/", views.archived_purchase_orders, name="archived_purchase_orders"),
    path("restore/", views.restore_purchase_orders, name="restore_purchase_orders"),
    path("permanently-delete/", views.permanently_delete_purchase_orders, name="permanently_delete_purchase_orders"),

    # ✅ PO Billing routes (must be before <str:purchase_order_id>)
    path("billing/", views.po_billing_dashboard, name="po_billing_dashboard"),
    path("billing/order/<int:po_id>/", views.po_billing_order_detail, name="po_billing_order_detail"),
    path("billing/order/<int:po_id>/upload-payment-proof/", views.po_upload_payment_proof, name="po_upload_payment_proof"),

    # ✅ Purchase Order print/pdf routes
    path("purchase-order/<str:purchase_order_id>/print/", views.purchase_order_print_view, name="purchase_order_print"),
    path("purchase-order/<str:purchase_order_id>/pdf/", views.purchase_order_pdf, name="purchase_order_pdf"),

    # ====== Dynamic route LAST ======
    path("<str:purchase_order_id>/", views.purchase_order_detail, name="purchase_order_detail"),
    path("<str:purchase_order_id>/confirm/", views.po_confirm_view, name="po_confirm"),
    path("<str:purchase_order_id>/receive/", views.po_receive_view, name="po_receive"),
     path('<str:purchase_order_id>/mark-in-transit/', 
         views.po_mark_in_transit, 
         name='po_mark_in_transit'),
    
    path('<str:purchase_order_id>/request-refund/', 
         views.po_request_refund, 
         name='po_request_refund'),
]
