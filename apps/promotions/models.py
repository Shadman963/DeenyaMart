"""Promotions models: Coupon and PromotionalCampaign."""
from decimal import Decimal
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


class Coupon(TimeStampedModel):
    """Discount coupon supporting percentage or fixed BDT deduction."""

    DISCOUNT_TYPES = [
        ("PERCENTAGE", "Percentage (%)"),
        ("FIXED", "Fixed Amount (৳)"),
    ]

    code = models.CharField(max_length=40, unique=True, db_index=True)
    description = models.CharField(max_length=160, blank=True)
    discount_type = models.CharField(max_length=16, choices=DISCOUNT_TYPES, default="PERCENTAGE")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.code
