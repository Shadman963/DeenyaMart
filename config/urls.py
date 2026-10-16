"""Main URL configuration for DeenyaMart."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls", namespace="core")),
    path("", include("apps.catalog.urls", namespace="catalog")),
    path("cart/", include("apps.cart.urls", namespace="cart")),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("", include("apps.orders.urls", namespace="orders")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
