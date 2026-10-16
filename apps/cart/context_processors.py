"""Context processor injecting cart item counts and summary into all templates."""
from .services import CartService


def cart_context(request):
    cart = CartService.get_or_create_cart(request, create=False)
    summary = CartService.get_summary(cart)
    return {
        "cart_summary": summary,
        "cart_count": summary["total_items"],
    }
