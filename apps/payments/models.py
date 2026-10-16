"""Payment transaction ledger models."""
import uuid
from django.db import models
from apps.core.models import TimeStampedModel


class PaymentTransaction(TimeStampedModel):
    """Transaction record for COD or gateway payments."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    provider = models.CharField(max_length=24, default="COD")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="BDT")
    status = models.CharField(max_length=24, default="PENDING")

    def __str__(self) -> str:
        return f"{self.provider} {self.amount} ({self.status})"
