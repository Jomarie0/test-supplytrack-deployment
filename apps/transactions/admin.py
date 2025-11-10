from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "object_repr")
    list_filter = ("action", "user")
    search_fields = ("object_repr", "object_id", "extra", "changes")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    ordering = ("-timestamp",)
    list_per_page = 50


# @admin.register(Transaction)
# # class TransactionAdmin(admin.ModelAdmin):
# #     list_display = ('timestamp', 'user', 'transaction_type', 'amount', 'reference')
# #     list_filter = ('transaction_type', 'user')
# #     search_fields = ('reference','notes')
# #     readonly_fields = [f.name for f in Transaction._meta.fields]
# #     ordering = ('-timestamp',)
