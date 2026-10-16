"""Cart service layer for adding, updating, removing, and summarizing cart items."""
from decimal import Decimal
from django.shortcuts import get_object_or_404
from apps.catalog.models import ProductVariant
from .models import Cart, CartItem


class CartService:
    """Centralized business logic for session and user carts."""

    SESSION_CART_ID_KEY = "dm_cart_id"

    @classmethod
    def get_or_create_cart(cls, request, create: bool = False):
        if not request.session.session_key and create:
            request.session.create()

        cart = None
        if request.user.is_authenticated:
            cart = Cart.objects.filter(user=request.user).first()

        cart_id = request.session.get(cls.SESSION_CART_ID_KEY)
        if not cart and cart_id:
            try:
                cart = Cart.objects.filter(pk=cart_id).first()
            except ValueError:
                cart = None

        if not cart and request.session.session_key:
            cart = Cart.objects.filter(session_key=request.session.session_key).first()

        if not cart and create:
            cart = Cart.objects.create(
                user=request.user if request.user.is_authenticated else None,
                session_key=request.session.session_key or "",
            )
            request.session[cls.SESSION_CART_ID_KEY] = str(cart.pk)
        elif cart:
            request.session[cls.SESSION_CART_ID_KEY] = str(cart.pk)

        return cart

    @classmethod
    def add_item(cls, request, variant_id: int, quantity: int = 1):
        variant = get_object_or_404(
            ProductVariant.objects.select_related("product"),
            pk=variant_id,
            is_active=True,
            product__is_active=True,
        )
        if variant.stock_qty <= 0:
            return None, None, 0, f"{variant.product.name} is currently out of stock."

        cart = cls.get_or_create_cart(request, create=True)
        item, created = CartItem.objects.get_or_create(
            cart=cart,
            variant=variant,
            defaults={"quantity": 0},
        )
        desired_qty = item.quantity + max(1, int(quantity))
        capped_qty = min(desired_qty, variant.stock_qty)
        added = capped_qty - item.quantity
        item.quantity = capped_qty
        item.save()

        if added <= 0:
            msg = f"Maximum available stock ({variant.stock_qty}) for {variant.product.name} is already in your cart."
        else:
            msg = f"Added {variant.product.name} ({variant.display_label}) to your bag."
        return cart, item, added, msg

    @classmethod
    def update_item(cls, request, item_id: int, quantity: int):
        cart = cls.get_or_create_cart(request, create=False)
        if not cart:
            return None, "Cart not found."
        item = CartItem.objects.select_related("variant__product").filter(
            cart=cart, pk=item_id
        ).first()
        if not item:
            return cart, "Item not found in your bag."

        if quantity <= 0:
            name = item.variant.product.name
            item.delete()
            return cart, f"Removed {name} from your bag."

        capped = min(int(quantity), item.variant.stock_qty)
        item.quantity = max(1, capped)
        item.save()
        return cart, f"Updated quantity for {item.variant.product.name}."

    @classmethod
    def get_summary(cls, cart) -> dict:
        if not cart:
            return {
                "cart": None,
                "items": [],
                "total_items": 0,
                "subtotal": Decimal("0.00"),
                "total_savings": Decimal("0.00"),
            }
        items = list(
            cart.items.select_related(
                "variant",
                "variant__product",
                "variant__product__category",
            ).prefetch_related("variant__product__images")
        )
        total_items = sum(i.quantity for i in items)
        subtotal = sum((i.line_total for i in items), Decimal("0.00"))
        total_savings = Decimal("0.00")
        for i in items:
            cmp_price = i.compare_at_price
            if cmp_price and cmp_price > i.unit_price:
                total_savings += (cmp_price - i.unit_price) * Decimal(i.quantity)
        return {
            "cart": cart,
            "items": items,
            "total_items": total_items,
            "subtotal": subtotal,
            "total_savings": total_savings,
        }
