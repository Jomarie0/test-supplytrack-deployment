from django.urls import path
from . import views

app_name = "delivery"

urlpatterns = [
    path("list/", views.delivery_list, name="delivery_list"),
    path("confirm/<int:delivery_id>/", views.confirm_delivery, name="confirm_delivery"),
    path("add/", views.add_delivery, name="add_delivery"),
    path(
        "update_status/<int:delivery_id>/",
        views.update_delivery_status_view,
        name="update_delivery_status",
    ),
    # Upload proof endpoint (saves image and optionally marks delivered)
    path("upload-proof/<int:delivery_id>/", views.upload_proof, name="upload_proof"),
    path(
        "detail/<int:delivery_id>/", views.delivery_detail, name="delivery_detail"
    ),  # NEW
    path("archive/", views.archive_list, name="archive_list"),
    path("archive_deliveries/", views.archive_deliveries, name="archive_deliveries"),
    path("restore_deliveries/", views.restore_deliveries, name="restore_deliveries"),
    path(
        "permanent_delete/",
        views.permanently_delete_deliveries,
        name="permanently_delete_deliveries",
    ),
]
