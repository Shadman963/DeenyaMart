"""Shipping zone models for transparent Bangladesh delivery rates."""
from decimal import Decimal
from django.db import models
from apps.core.models import TimeStampedModel


class ShippingZone(TimeStampedModel):
    """Configurable delivery zone (e.g., Inside Dhaka, Dhaka Sub-Area, Outside Dhaka)."""

    code = models.CharField(max_length=32, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    coverage_summary = models.CharField(max_length=200, blank=True)
    base_rate = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("60.00"))
    free_shipping_threshold = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    estimated_delivery_days = models.CharField(max_length=60, default="1–2 Business Days")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "base_rate"]

    def __str__(self) -> str:
        return f"{self.name} (৳{self.base_rate:,.0f})"
