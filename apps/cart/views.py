"""Cart views for full page cart, slide-over drawer partial, and async add/update/remove."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views import View

from .services import CartService


class CartDetailView(View):
    """Full shopping bag page (/cart/)."""

    def get(self, request):
        cart = CartService.get_or_create_cart(request, create=False)
        summary = CartService.get_summary(cart)
        return render(request, "cart/cart_detail.html", {"cart_summary": summary})


class CartDrawerPartialView(View):
    """HTML partial for the slide-over cart drawer."""

    def get(self, request):
        cart = CartService.get_or_create_cart(request, create=False)
        summary = CartService.get_summary(cart)
        return render(request, "cart/partials/cart_drawer_body.html", {"cart_summary": summary})


class CartAddView(View):
    """Adds a ProductVariant to the active cart (supports progressive enhancement fetch + form POST)."""

    def post(self, request):
        variant_id = request.POST.get("variant_id")
        try:
            quantity = max(1, int(request.POST.get("quantity", 1)))
        except (TypeError, ValueError):
            quantity = 1

        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
        )

        if not variant_id:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Please select a product option."}, status=400)
            messages.error(request, "Please select a product option.")
            return redirect(request.META.get("HTTP_REFERER", "/"))

        cart, item, added, msg = CartService.add_item(request, int(variant_id), quantity)
        summary = CartService.get_summary(cart)

        if wants_json:
            drawer_html = render_to_string(
                "cart/partials/cart_drawer_body.html",
                {"cart_summary": summary},
                request=request,
            )
            return JsonResponse(
                {
                    "ok": added > 0,
                    "added": added,
                    "message": msg,
                    "cart_count": summary["total_items"],
                    "subtotal": f"৳{summary['subtotal']:,.0f}",
                    "drawer_html": drawer_html,
                },
                status=200 if added > 0 else 400,
            )

        if added > 0:
            messages.success(request, msg)
        else:
            messages.warning(request, msg)

        if request.POST.get("buy_now") == "1" and added > 0:
            return redirect("orders:checkout")

        return redirect(request.META.get("HTTP_REFERER", "cart:detail"))


class CartUpdateQtyView(View):
    """Updates quantity or removes a line item in the cart."""

    def post(self, request):
        item_id = request.POST.get("item_id")
        try:
            quantity = int(request.POST.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0

        cart, msg = CartService.update_item(request, int(item_id), quantity)
        summary = CartService.get_summary(cart)

        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
        )
        if wants_json:
            drawer_html = render_to_string(
                "cart/partials/cart_drawer_body.html",
                {"cart_summary": summary},
                request=request,
            )
            return JsonResponse(
                {
                    "ok": True,
                    "message": msg,
                    "cart_count": summary["total_items"],
                    "subtotal": f"৳{summary['subtotal']:,.0f}",
                    "drawer_html": drawer_html,
                }
            )

        messages.info(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "cart:detail"))
