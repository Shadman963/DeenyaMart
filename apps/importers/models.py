"""Models for tracking product catalog imports, supplier category mappings, and audit logs."""
import uuid
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


class SupplierCategoryMap(TimeStampedModel):
    """Normalizes supplier category IDs, slugs, and names/typos to clean storefront categories."""

    source_name = models.CharField(max_length=40, default="droploo", db_index=True)
    external_category_id = models.CharField(max_length=64, blank=True, default="")
    external_subcategory_id = models.CharField(max_length=64, blank=True, default="")
    external_name = models.CharField(max_length=150, blank=True, default="")
    external_slug = models.CharField(max_length=150, blank=True, default="")
    mapped_category = models.ForeignKey(
        "catalog.Category",
        on_delete=models.CASCADE,
        related_name="supplier_mappings",
    )

    class Meta:
        ordering = ["source_name", "external_name", "external_category_id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source_name",
                    "external_category_id",
                    "external_subcategory_id",
                    "external_name",
                ],
                name="uniq_supplier_cat_map",
            )
        ]

    def __init__(self, *args, **kwargs):
        if "external_category_id" in kwargs and kwargs["external_category_id"] is not None:
            kwargs["external_category_id"] = str(kwargs["external_category_id"]).strip()
        elif "external_category_id" in kwargs and kwargs["external_category_id"] is None:
            kwargs["external_category_id"] = ""
        if "external_subcategory_id" in kwargs and kwargs["external_subcategory_id"] is not None:
            kwargs["external_subcategory_id"] = str(kwargs["external_subcategory_id"]).strip()
        elif "external_subcategory_id" in kwargs and kwargs["external_subcategory_id"] is None:
            kwargs["external_subcategory_id"] = ""
        super().__init__(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.external_category_id = (
            str(self.external_category_id).strip() if self.external_category_id is not None else ""
        )
        self.external_subcategory_id = (
            str(self.external_subcategory_id).strip()
            if self.external_subcategory_id is not None
            else ""
        )
        self.external_name = (self.external_name or "").strip()
        self.external_slug = (self.external_slug or "").strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        ext_label = self.external_name or self.external_category_id or self.external_slug
        return f"{self.source_name}:{ext_label} → {self.mapped_category.name}"


class SupplierProductMap(TimeStampedModel):
    """Links an external supplier product ID to a local Product record while separating supplier cost from retail price."""

    source_name = models.CharField(max_length=40, default="droploo", db_index=True)
    external_product_id = models.CharField(max_length=128, db_index=True)
    external_sku = models.CharField(max_length=128, blank=True, default="")
    external_slug = models.CharField(max_length=280, blank=True, default="")
    product = models.OneToOneField(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="supplier_map",
    )
    supplier_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Supplier wholesale/buy cost in store currency (BDT).",
    )
    supplier_suggested_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional supplier reference/suggested price (never assumed as store retail price).",
    )
    applied_margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Computed retail gross margin percentage at import time.",
    )
    raw_payload_hash = models.CharField(max_length=64, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-last_synced_at", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_name", "external_product_id"],
                name="uniq_supplier_product_map_ext_id",
            )
        ]

    def __init__(self, *args, **kwargs):
        if "external_product_id" in kwargs and kwargs["external_product_id"] is not None:
            kwargs["external_product_id"] = str(kwargs["external_product_id"]).strip()
        super().__init__(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.external_product_id = str(self.external_product_id).strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.source_name}:{self.external_product_id} → {self.product.name}"


class ImportJobLog(TimeStampedModel):
    """Audit log with timestamps, summary metrics, and row-level error/warning reports for catalog import runs."""

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS", "Completed with Errors"
        DRY_RUN = "DRY_RUN", "Dry Run (Simulated)"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_name = models.CharField(max_length=64, default="droploo", db_index=True)
    source_type = models.CharField(max_length=32, default="CSV_FILE")
    source_reference = models.CharField(max_length=255)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.RUNNING,
        db_index=True,
    )
    is_dry_run = models.BooleanField(default=False)
    overwrite_blank = models.BooleanField(default=False)
    download_images = models.BooleanField(default=False)

    total_records = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    unchanged_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    row_errors = models.JSONField(default=list, blank=True)
    row_warnings = models.JSONField(default=list, blank=True)
    summary_data = models.JSONField(default=dict, blank=True)

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at", "-created_at"]

    def __str__(self) -> str:
        mode = "DRY-RUN" if self.is_dry_run else self.source_type
        return f"ImportJob {self.id} [{mode}] ({self.status})"
