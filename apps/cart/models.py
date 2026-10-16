"""Shopping cart models supporting anonymous session carts and authenticated users."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Cart(TimeStampedModel):
    """Persistent or session-backed shopping cart."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart",
    )
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    coupon = models.ForeignKey(
        "promotions.Coupon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    shipping_zone = models.ForeignKey(
        "shipping.ShippingZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self) -> str:
        return f"Cart {self.id}"


class CartItem(TimeStampedModel):
    """Line item in a shopping cart referencing a specific ProductVariant."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    quantity = models.PositiveSmallIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "variant"],
                name="uniq_cart_variant",
            )
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.quantity}x {self.variant}"

    @property
    def unit_price(self) -> Decimal:
        return self.variant.effective_price

    @property
    def compare_at_price(self):
        eff = self.unit_price
        if (
            self.variant.compare_at_price
            and self.variant.compare_at_price > eff
        ):
            return self.variant.compare_at_price
        if self.variant.product.has_valid_discount and self.variant.product.original_price > eff:
            return self.variant.product.original_price
        return None

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * Decimal(self.quantity)

