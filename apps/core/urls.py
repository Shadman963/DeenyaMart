"""URL configuration for core app."""
from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("newsletter/subscribe/", views.NewsletterSubscribeView.as_view(), name="newsletter_subscribe"),
    path("pages/<slug:slug>/", views.ContentPageDetailView.as_view(), name="page_detail"),
    path("contact/", views.ContactView.as_view(), name="contact"),
    path("healthz/", views.HealthCheckView.as_view(), name="healthz"),
]
