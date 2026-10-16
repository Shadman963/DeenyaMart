"""URL configuration for cart app."""
from django.urls import path
from . import views

app_name = "cart"

urlpatterns = [
    path("", views.CartDetailView.as_view(), name="detail"),
    path("drawer/", views.CartDrawerPartialView.as_view(), name="drawer_partial"),
    path("add/", views.CartAddView.as_view(), name="add"),
    path("update/", views.CartUpdateQtyView.as_view(), name="update"),
]
