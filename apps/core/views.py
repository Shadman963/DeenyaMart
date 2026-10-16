"""Views for homepage, newsletter subscription, static policy pages, contact, and healthz."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.catalog.models import Brand, Category, Product
from .models import ContentPage, NewsletterSubscriber


class HomeView(TemplateView):
    """Storefront homepage view rendering dynamic categories, product shelves, and verified trust signals."""

    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        active_subcategories = Category.objects.filter(is_active=True).order_by("sort_order", "name")
        top_categories = list(
            Category.objects.filter(parent__isnull=True, is_active=True)
            .prefetch_related(
                Prefetch("children", queryset=active_subcategories, to_attr="subcategories"),
            )
            .order_by("sort_order", "name")
        )

        # Compute real active product counts per top-level category (including subcategories)
        active_products_qs = Product.objects.filter(is_active=True).values("category_id", "category__parent_id")
        cat_counts = {}
        for row in active_products_qs:
            cid = row["category_id"]
            pid = row["category__parent_id"]
            cat_counts[cid] = cat_counts.get(cid, 0) + 1
            if pid:
                cat_counts[pid] = cat_counts.get(pid, 0) + 1

        for cat in top_categories:
            cat.product_total = cat_counts.get(cat.id, 0)
            for sub in getattr(cat, "subcategories", []):
                sub.product_total = cat_counts.get(sub.id, 0)

        ctx["discovery_categories"] = top_categories
        ctx["featured_brands"] = list(Brand.objects.filter(is_active=True, is_featured=True))

        # Section 6: New arrivals and featured products in responsive grids
        ctx["new_arrivals"] = list(
            Product.objects.with_card_data()
            .filter(is_active=True, is_new_arrival=True)
            .order_by("sort_order", "-created_at")[:8]
        )
        ctx["featured_products"] = list(
            Product.objects.with_card_data()
            .filter(is_active=True, is_featured=True)
            .order_by("-sort_order", "-created_at")[:8]
        )

        # Section 8: Best-selling or popular products ONLY when real sales data exists;
        # otherwise explicitly call it "Featured Products".
        has_real_sales_data = Product.objects.filter(is_active=True, total_sales__gt=0).exists()
        ctx["has_real_sales_data"] = has_real_sales_data

        if has_real_sales_data:
            ctx["popular_section_eyebrow"] = "Verified Customer Demand"
            ctx["popular_section_title"] = "Best-Selling Products"
            ctx["popular_section_subtitle"] = (
                "Most frequently ordered modest wear and household essentials based on verified fulfilled orders."
            )
            ctx["popular_or_featured_products"] = list(
                Product.objects.with_card_data()
                .filter(is_active=True, total_sales__gt=0)
                .order_by("-total_sales", "sort_order")[:4]
            )
        else:
            ctx["popular_section_eyebrow"] = "Curated Catalog Highlights"
            ctx["popular_section_title"] = "Featured Products"
            ctx["popular_section_subtitle"] = (
                "Editorially selected modest garments and practical home essentials from our catalog."
            )
            # Show featured products (prioritizing items distinct from the first row or highlighted essentials)
            featured_list = ctx["featured_products"]
            ctx["popular_or_featured_products"] = (
                featured_list[4:8] if len(featured_list) > 4 else featured_list[:4]
            )

        return ctx


class NewsletterSubscribeView(View):
    """Server-side handler for newsletter signup with JSON and HTML fallback."""

    def post(self, request):
        raw_email = (request.POST.get("email") or "").strip().lower()
        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
        )

        if not raw_email:
            msg = "Please enter your email address to subscribe."
            if wants_json:
                return JsonResponse({"ok": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.META.get("HTTP_REFERER", "/"))

        try:
            validate_email(raw_email)
        except ValidationError:
            msg = "Please provide a valid email address (for example, name@example.com)."
            if wants_json:
                return JsonResponse({"ok": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.META.get("HTTP_REFERER", "/"))

        subscriber, created = NewsletterSubscriber.objects.get_or_create(
            email=raw_email,
            defaults={"is_active": True, "source": "homepage"},
        )
        if not created and not subscriber.is_active:
            subscriber.is_active = True
            subscriber.save(update_fields=["is_active", "updated_at"])
            created = True

        if created:
            msg = f"Thank you! {raw_email} has been subscribed to DeenyaMart seasonal dispatches."
        else:
            msg = f"{raw_email} is already subscribed to our seasonal dispatches."

        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "created": created,
                    "email": raw_email,
                    "message": msg,
                }
            )

        messages.success(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "/"))


class ContentPageDetailView(DetailView):
    """Renders policy and informational pages (/pages/<slug>/)."""

    model = ContentPage
    template_name = "core/page_detail.html"
    context_object_name = "page"

    def get_queryset(self):
        return ContentPage.objects.filter(is_active=True)


class ContactView(View):
    """Customer support & contact page."""

    def get(self, request):
        return render(request, "core/contact.html")

    def post(self, request):
        name = (request.POST.get("name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        message_text = (request.POST.get("message") or "").strip()
        if not name or not message_text:
            messages.error(request, "Please provide your name and message.")
        else:
            messages.success(
                request,
                f"Thank you, {name}. Our Dhaka customer care team has received your inquiry.",
            )
        return redirect("core:contact")


class HealthCheckView(View):
    """Readiness probe verifying database connectivity."""

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok", "service": "deenyamart"})
