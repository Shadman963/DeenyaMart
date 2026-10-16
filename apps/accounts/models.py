"""Models for customer accounts, addresses, OTP verification, and wishlist."""
from django.contrib.auth.models import AbstractUser
from django.db import models
from apps.core.models import TimeStampedModel


class User(AbstractUser):
    """Custom user supporting Bangladesh mobile numbers and email."""

    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_phone_verified = models.BooleanField(default=False)
    loyalty_points = models.PositiveIntegerField(default=0)

    def get_display_name(self) -> str:
        return self.full_name or self.get_full_name() or self.username


class CustomerAddress(TimeStampedModel):
    """Saved delivery address for a customer."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=40, default="Home")
    recipient_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20)
    division = models.CharField(max_length=80, default="Dhaka")
    district = models.CharField(max_length=80, default="Dhaka")
    thana_upazila = models.CharField(max_length=80, blank=True)
    street_address = models.TextField()
    postal_code = models.CharField(max_length=16, blank=True)
    shipping_zone = models.ForeignKey(
        "shipping.ShippingZone",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self) -> str:
        return f"{self.recipient_name} ({self.label}) - {self.district}"


class WishlistItem(TimeStampedModel):
    """Saved wishlist item for authenticated users (session fallback supported in views)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wishlist_items")
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="wishlisted_by",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="uniq_user_wishlist_product",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} -> {self.product}"
