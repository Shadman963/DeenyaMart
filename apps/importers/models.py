"""Models for tracking authorized Droploo catalog imports and category mappings."""
import uuid
from django.db import models
from apps.core.models import TimeStampedModel


class SupplierCategoryMap(TimeStampedModel):
    """Normalizes supplier category IDs and typos to clean storefront categories."""

    source_name = models.CharField(max_length=40, default="droploo")
    external_category_id = models.IntegerField()
    external_subcategory_id = models.IntegerField(null=True, blank=True)
    external_name = models.CharField(max_length=150)
    mapped_category = models.ForeignKey(
        "catalog.Category",
        on_delete=models.CASCADE,
        related_name="supplier_mappings",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_name", "external_category_id", "external_subcategory_id"],
                name="uniq_supplier_cat_map",
            )
        ]


class SupplierProductMap(TimeStampedModel):
    """Links an external supplier product ID to a local Product record."""

    source_name = models.CharField(max_length=40, default="droploo")
    external_product_id = models.IntegerField(db_index=True)
    external_slug = models.CharField(max_length=280)
    product = models.OneToOneField(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="supplier_map",
    )
    raw_payload_hash = models.CharField(max_length=64, blank=True)


class ImportJobLog(TimeStampedModel):
    """Audit log for catalog import runs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_type = models.CharField(max_length=32, default="JSON_FILE")
    source_reference = models.CharField(max_length=255)
    status = models.CharField(max_length=32, default="COMPLETED")
    total_records = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
