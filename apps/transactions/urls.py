from django.urls import path
from . import views

app_name = "transactions"

urlpatterns = [
    path("auditlog/", views.auditlog_list, name="auditlog_list"),
    path("auditlog/clear/", views.clear_audit_logs, name="clear_audit_logs"),  # 👈 new
]
