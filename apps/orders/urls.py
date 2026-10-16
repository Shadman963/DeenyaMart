"""URL configuration for orders and tracking."""
from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("track-order/", views.OrderTrackView.as_view(), name="track"),
    path("track-order/<str:order_number>/", views.OrderTrackView.as_view(), name="track_detail"),
]
