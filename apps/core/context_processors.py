"""Global storefront context processor."""
from django.db.models import Prefetch
from apps.accounts.models import WishlistItem
from apps.catalog.models import Category, Product
from apps.shipping.models import ShippingZone
from .models import SiteSettings


def site_context(request):
    """Provides site settings, dynamic category hierarchy, shipping facts, and wishlist state."""
    try:
        site_settings = SiteSettings.get_solo()
    except Exception:
        site_settings = None

    active_children = Category.objects.filter(is_active=True).order_by("sort_order", "name")
    nav_categories = list(
        Category.objects.filter(parent__isnull=True, is_active=True)
        .prefetch_related(Prefetch("children", queryset=active_children, to_attr="active_children"))
        .order_by("sort_order", "name")
    )

    shipping_zones = list(ShippingZone.objects.filter(is_active=True).order_by("sort_order", "base_rate"))

    session_wishlist = set(request.session.get("dm_wishlist_ids", []))
    if request.user.is_authenticated:
        db_wishlist = set(
            WishlistItem.objects.filter(user=request.user).values_list("product_id", flat=True)
        )
        wishlist_ids = session_wishlist | db_wishlist
    else:
        wishlist_ids = session_wishlist

    has_dev_fixtures = Product.objects.filter(is_active=True, is_development_fixture=True).exists()

    return {
        "site_settings": site_settings,
        "nav_categories": nav_categories,
        "shipping_zones": shipping_zones,
        "wishlist_product_ids": wishlist_ids,
        "wishlist_count": len(wishlist_ids),
        "has_dev_fixtures": has_dev_fixtures,
    }
