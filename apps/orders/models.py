"""Order models with immutable product/price snapshots and status history."""
from decimal import Decimal
from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Order(TimeStampedModel):
    """Customer order record."""

    STATUS_CHOICES = [
        ("PENDING_OTP", "Pending Verification"),
        ("CONFIRMED", "Confirmed"),
        ("PROCESSING", "Processing"),
        ("PACKED", "Packed"),
        ("HANDED_TO_COURIER", "Picked Up by Courier"),
        ("IN_TRANSIT", "On the Way"),
        ("DELIVERED", "Delivered"),
        ("CANCELLED", "Cancelled"),
    ]

    PAYMENT_METHODS = [
        ("COD", "Cash on Delivery"),
    ]

    order_number = models.CharField(max_length=28, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=24, db_index=True)
    customer_email = models.EmailField(blank=True)
    shipping_address_line = models.TextField()
    shipping_area = models.CharField(max_length=100, blank=True)
    shipping_district = models.CharField(max_length=80, default="Dhaka")
    shipping_zone = models.ForeignKey(
        "shipping.ShippingZone",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    order_notes = models.TextField(blank=True)

    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2)
    grand_total = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="CONFIRMED")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="COD")
    payment_status = models.CharField(max_length=20, default="UNPAID")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.order_number


class OrderItem(TimeStampedModel):
    """Line item snapshot within an Order."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT)
    product_name_snapshot = models.CharField(max_length=255)
    variant_label_snapshot = models.CharField(max_length=120)
    sku_snapshot = models.CharField(max_length=80)
    unit_retail_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveSmallIntegerField(default=1)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.quantity}x {self.product_name_snapshot}"
