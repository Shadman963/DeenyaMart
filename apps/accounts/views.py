"""Customer account and wishlist views (supporting both anonymous session and logged-in users)."""
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.catalog.models import Product
from .models import User, WishlistItem


class WishlistView(View):
    """Displays saved wishlist products."""

    def get(self, request):
        session_ids = set(request.session.get("dm_wishlist_ids", []))
        if request.user.is_authenticated:
            db_ids = set(
                WishlistItem.objects.filter(user=request.user).values_list("product_id", flat=True)
            )
            product_ids = session_ids | db_ids
        else:
            product_ids = session_ids

        products = list(
            Product.objects.with_card_data().filter(id__in=product_ids, is_active=True)
        )
        return render(request, "accounts/wishlist.html", {"wishlist_products": products})


class WishlistToggleView(View):
    """Toggles a product in the customer's wishlist (session + DB if authenticated)."""

    def post(self, request):
        product_id = request.POST.get("product_id")
        product = get_object_or_404(Product, pk=product_id, is_active=True)

        session_ids = list(request.session.get("dm_wishlist_ids", []))
        if product.id in session_ids:
            session_ids.remove(product.id)
            added = False
        else:
            session_ids.append(product.id)
            added = True
        request.session["dm_wishlist_ids"] = session_ids
        request.session.modified = True

        if request.user.is_authenticated:
            if added:
                WishlistItem.objects.get_or_create(user=request.user, product=product)
            else:
                WishlistItem.objects.filter(user=request.user, product=product).delete()
            total_count = len(
                set(session_ids)
                | set(WishlistItem.objects.filter(user=request.user).values_list("product_id", flat=True))
            )
        else:
            total_count = len(session_ids)

        msg = (
            f"Saved {product.name} to your wishlist."
            if added
            else f"Removed {product.name} from your wishlist."
        )

        wants_json = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
        )
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "added": added,
                    "product_id": product.id,
                    "wishlist_count": total_count,
                    "message": msg,
                }
            )

        messages.info(request, msg)
        return redirect(request.META.get("HTTP_REFERER", "accounts:wishlist"))


class AccountOverviewView(View):
    """Customer account portal & sign-in page."""

    def get(self, request):
        return render(request, "accounts/dashboard.html")

    def post(self, request):
        action = request.POST.get("action", "login")
        if action == "logout":
            logout(request)
            messages.info(request, "You have been signed out.")
            return redirect("core:home")

        identifier = (request.POST.get("identifier") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=identifier, password=password)
        if not user and identifier:
            matched_user = User.objects.filter(phone_number=identifier).first() or User.objects.filter(email=identifier).first()
            if matched_user:
                user = authenticate(request, username=matched_user.username, password=password)

        if user:
            login(request, user)
            messages.success(request, f"Welcome back, {user.get_display_name()}.")
            return redirect("accounts:dashboard")

        messages.error(request, "Invalid phone/email or password. Please check your credentials.")
        return render(request, "accounts/dashboard.html")
