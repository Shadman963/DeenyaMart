"""URL configuration for accounts and wishlist."""
from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("dashboard/", views.AccountOverviewView.as_view(), name="dashboard"),
    path("login/", views.AccountOverviewView.as_view(), name="login"),
    path("wishlist/", views.WishlistView.as_view(), name="wishlist"),
    path("wishlist/toggle/", views.WishlistToggleView.as_view(), name="wishlist_toggle"),
]
