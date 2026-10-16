"""URL configuration for catalog discovery, categories, products, and search."""
from django.urls import path
from . import views

app_name = "catalog"

urlpatterns = [
    path("shop/", views.ProductListView.as_view(), name="product_list"),
    path("category/<slug:slug>/", views.CategoryDetailView.as_view(), name="category_detail"),
    path("product/<slug:slug>/", views.ProductDetailView.as_view(), name="product_detail"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("search/suggestions/", views.SearchSuggestionsView.as_view(), name="search_suggestions"),
]
