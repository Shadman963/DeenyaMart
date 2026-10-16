"""Catalog views: shop listing, category detail, product detail, search, and live suggestions."""
from django.conf import settings
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.views import View
from django.views.generic import DetailView, ListView

from .forms import CatalogFilterForm
from .models import Brand, Category, Product
from .services import CatalogQueryService


class BaseCatalogListView(ListView):
    """Shared base ListView for faceted catalog browsing, category pages, and search results."""

    model = Product
    context_object_name = "products"
    paginate_by = getattr(settings, "CATALOG_PAGINATE_BY", 12)

    def get_paginate_by(self, queryset):
        return getattr(settings, "CATALOG_PAGINATE_BY", 12)

    def _populate_filter_context(self, ctx: dict, params: dict) -> dict:
        ctx["brands"] = list(Brand.objects.filter(is_active=True).order_by("name"))
        ctx["categories"] = list(
            Category.objects.active().top_level().order_by("sort_order", "name")
        )
        ctx["sort_choices"] = CatalogFilterForm.SORT_CHOICES
        ctx["current_sort"] = params["sort"]
        ctx["current_fabric"] = params["brand"]
        ctx["current_brand"] = params["brand"]
        ctx["current_category"] = params["category"]
        ctx["selected_category"] = params["selected_category"]
        ctx["min_price"] = params["min_price"]
        ctx["max_price"] = params["max_price"]
        ctx["current_availability"] = params["availability"]
        ctx["in_stock_filter"] = params["in_stock"]
        ctx["on_sale_filter"] = params["on_sale"]
        ctx["search_query"] = params["q"]
        ctx["filter_errors"] = params["errors"]
        ctx["pagination_query_string"] = params["pagination_query_string"]
        return ctx


class ProductListView(BaseCatalogListView):
    """Faceted catalog listing (/shop/)."""

    template_name = "catalog/product_list.html"

    def get_queryset(self):
        qs, self.filter_params = CatalogQueryService.filter_products(self.request.GET)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        return self._populate_filter_context(ctx, self.filter_params)


class CategoryDetailView(BaseCatalogListView):
    """Category landing page (/category/<slug>/)."""

    template_name = "catalog/category_detail.html"

    def get_queryset(self):
        self.category = (
            Category.objects.active()
            .select_related("parent")
            .filter(slug=self.kwargs["slug"])
            .first()
        )
        if not self.category:
            raise Http404("Category not found or inactive.")
        qs, self.filter_params = CatalogQueryService.filter_products(
            self.request.GET,
            base_category=self.category,
        )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        self._populate_filter_context(ctx, self.filter_params)
        ctx["category"] = self.category
        if self.category.parent_id:
            ctx["parent_category"] = self.category.parent
            ctx["sibling_categories"] = list(
                Category.objects.filter(parent=self.category.parent, is_active=True).order_by(
                    "sort_order", "name"
                )
            )
            ctx["subcategories"] = []
        else:
            ctx["parent_category"] = None
            ctx["subcategories"] = list(
                Category.objects.filter(parent=self.category, is_active=True).order_by(
                    "sort_order", "name"
                )
            )
        return ctx


class ProductDetailView(DetailView):
    """Product Detail Page (/product/<slug>/)."""

    model = Product
    template_name = "catalog/product_detail.html"
    context_object_name = "product"

    def get_queryset(self):
        return (
            Product.objects.with_card_data()
            .active()
            .prefetch_related("sources", "reviews")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        product = self.object
        ctx["variants"] = [v for v in product.variants.all() if v.is_active]
        ctx["gallery_images"] = list(product.images.all())
        ctx["approved_reviews"] = [r for r in product.reviews.all() if r.is_approved]
        parent_or_self_id = product.category.parent_id or product.category_id
        ctx["related_products"] = list(
            Product.objects.with_card_data()
            .active()
            .filter(
                Q(category=product.category) | Q(category__parent_id=parent_or_self_id),
            )
            .exclude(pk=product.pk)[:4]
        )
        return ctx


class SearchView(ProductListView):
    """Search results page (/search/?q=...)."""

    template_name = "catalog/search_results.html"


class SearchSuggestionsView(View):
    """Live JSON autocomplete endpoint (/search/suggestions/?q=...)."""

    def get(self, request):
        q = (request.GET.get("q") or "").replace("\x00", "").strip()[:120]
        if len(q) < 1:
            return JsonResponse({"query": q, "products": [], "categories": []})

        currency_symbol = getattr(settings, "STORE_CURRENCY_SYMBOL", "৳")

        products = (
            Product.objects.with_card_data()
            .active()
            .search(q)[:5]
        )
        categories = Category.objects.active().filter(
            Q(name__icontains=q) | Q(subtitle__icontains=q) | Q(description__icontains=q),
        )[:3]

        product_results = []
        for p in products:
            img = p.primary_image
            product_results.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "sku": p.sku or p.sku_prefix,
                    "category": p.category.name,
                    "currency": p.currency,
                    "url": p.get_absolute_url(),
                    "price": f"{currency_symbol}{p.current_price:,.0f}",
                    "original_price": (
                        f"{currency_symbol}{p.original_price:,.0f}"
                        if p.original_price
                        else None
                    ),
                    "image_url": (
                        img.resolved_url
                        if img
                        else "/static/img/fixtures/lf104-primary.svg"
                    ),
                    "in_stock": p.is_in_stock,
                }
            )

        category_results = [
            {
                "name": c.name,
                "url": c.get_absolute_url(),
            }
            for c in categories
        ]

        return JsonResponse(
            {
                "query": q,
                "products": product_results,
                "categories": category_results,
            }
        )
