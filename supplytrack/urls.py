from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponseRedirect
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", lambda request: HttpResponseRedirect("/store/")),
    path("users/", include("apps.users.urls")),
    # path('users/', include('apps.users.urls')),
    path("inventory/", include("apps.inventory.urls")),
    path("orders/", include("apps.orders.urls")),
    path("purchase_orders/", include("apps.purchase_orders.urls")),
    path("delivery/", include("apps.delivery.urls")),  # Include delivery app URLs
    path("store/", include("apps.store.urls")),
    path("suppliers/", include("apps.suppliers.urls", namespace="suppliers")),
    path("transactions/", include("apps.transactions.urls", namespace="transactions")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
