"""Catalog views: shop listing, category detail, product detail, search, and live suggestions."""
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import DetailView, ListView

from .models import Brand, Category, Product


class ProductListView(ListView):
    """Faceted catalog listing (/shop/)."""

    model = Product
    template_name = "catalog/product_list.html"
    context_object_name = "products"
    paginate_by = 12

    def get_queryset(self):
        qs = Product.objects.with_card_data().filter(is_active=True)

        category_slug = (self.request.GET.get("category") or "").strip()
        if category_slug:
            cat = Category.objects.filter(slug=category_slug, is_active=True).first()
            if cat:
                qs = qs.filter(Q(category=cat) | Q(category__parent=cat))

        brand_slug = (self.request.GET.get("fabric") or self.request.GET.get("brand") or "").strip()
        if brand_slug:
            qs = qs.filter(brand__slug=brand_slug)

        if self.request.GET.get("on_sale") == "1":
            qs = qs.filter(base_sale_price__isnull=False)

        if self.request.GET.get("in_stock") == "1":
            qs = qs.filter(variants__stock_qty__gt=0).distinct()

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(sku_prefix__icontains=q)
                | Q(short_description__icontains=q)
                | Q(category__name__icontains=q)
                | Q(brand__name__icontains=q)
            )

        sort = self.request.GET.get("sort", "featured")
        if sort == "newest":
            qs = qs.order_by("-is_new_arrival", "-created_at")
        elif sort == "price_asc":
            qs = qs.order_by("base_regular_price")
        elif sort == "price_desc":
            qs = qs.order_by("-base_regular_price")
        else:
            qs = qs.order_by("sort_order", "-is_featured", "-created_at")

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["brands"] = Brand.objects.filter(is_active=True)
        ctx["current_sort"] = self.request.GET.get("sort", "featured")
        ctx["current_fabric"] = self.request.GET.get("fabric") or self.request.GET.get("brand") or ""
        ctx["current_category"] = self.request.GET.get("category", "")
        ctx["on_sale_filter"] = self.request.GET.get("on_sale") == "1"
        return ctx


class CategoryDetailView(ListView):
    """Category landing page (/category/<slug>/)."""

    model = Product
    template_name = "catalog/category_detail.html"
    context_object_name = "products"
    paginate_by = 12

    def get_queryset(self):
        self.category = get_object_or_404(Category, slug=self.kwargs["slug"], is_active=True)
        qs = Product.objects.with_card_data().filter(
            Q(category=self.category) | Q(category__parent=self.category),
            is_active=True,
        )
        sort = self.request.GET.get("sort", "featured")
        if sort == "newest":
            qs = qs.order_by("-is_new_arrival", "-created_at")
        elif sort == "price_asc":
            qs = qs.order_by("base_regular_price")
        elif sort == "price_desc":
            qs = qs.order_by("-base_regular_price")
        else:
            qs = qs.order_by("sort_order", "-created_at")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["category"] = self.category
        if self.category.parent_id:
            ctx["parent_category"] = self.category.parent
            ctx["sibling_categories"] = list(
                Category.objects.filter(parent=self.category.parent, is_active=True)
            )
            ctx["subcategories"] = []
        else:
            ctx["parent_category"] = None
            ctx["subcategories"] = list(
                Category.objects.filter(parent=self.category, is_active=True)
            )
        ctx["current_sort"] = self.request.GET.get("sort", "featured")
        return ctx


class ProductDetailView(DetailView):
    """Product Detail Page (/product/<slug>/)."""

    model = Product
    template_name = "catalog/product_detail.html"
    context_object_name = "product"

    def get_queryset(self):
        return Product.objects.with_card_data().filter(is_active=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        product = self.object
        ctx["variants"] = [v for v in product.variants.all() if v.is_active]
        ctx["gallery_images"] = list(product.images.all())
        ctx["related_products"] = list(
            Product.objects.with_card_data()
            .filter(
                Q(category=product.category) | Q(category__parent=product.category.parent_id or product.category_id),
                is_active=True,
            )
            .exclude(pk=product.pk)[:4]
        )
        return ctx


class SearchView(ProductListView):
    """Search results page (/search/?q=...)."""

    template_name = "catalog/search_results.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = (self.request.GET.get("q") or "").strip()
        return ctx


class SearchSuggestionsView(View):
    """Live JSON autocomplete endpoint (/search/suggestions/?q=...)."""

    def get(self, request):
        q = (request.GET.get("q") or "").strip()
        if len(q) < 1:
            return JsonResponse({"query": q, "products": [], "categories": []})

        products = (
            Product.objects.with_card_data()
            .filter(
                Q(name__icontains=q)
                | Q(sku_prefix__icontains=q)
                | Q(category__name__icontains=q)
                | Q(brand__name__icontains=q),
                is_active=True,
            )
            .distinct()[:5]
        )
        categories = Category.objects.filter(
            Q(name__icontains=q) | Q(subtitle__icontains=q),
            is_active=True,
        )[:3]

        product_results = []
        for p in products:
            img = p.primary_image
            product_results.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "sku": p.sku_prefix,
                    "category": p.category.name,
                    "url": p.get_absolute_url(),
                    "price": f"৳{p.current_price:,.0f}",
                    "original_price": f"৳{p.original_price:,.0f}" if p.original_price else None,
                    "image_url": img.image_url if img else "/static/img/fixtures/modest-khimar-primary.svg",
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
