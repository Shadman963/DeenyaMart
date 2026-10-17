"""Django Admin configuration for supplier category/product mappings and import audit logs."""
from django.contrib import admin
from .models import ImportJobLog, SupplierCategoryMap, SupplierProductMap


@admin.register(SupplierCategoryMap)
class SupplierCategoryMapAdmin(admin.ModelAdmin):
    list_display = (
        "source_name",
        "external_category_id",
        "external_subcategory_id",
        "external_name",
        "external_slug",
        "mapped_category",
        "updated_at",
    )
    list_filter = ("source_name", "mapped_category")
    search_fields = ("external_name", "external_slug", "external_category_id", "mapped_category__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SupplierProductMap)
class SupplierProductMapAdmin(admin.ModelAdmin):
    list_display = (
        "source_name",
        "external_product_id",
        "external_sku",
        "product",
        "supplier_cost",
        "supplier_suggested_price",
        "applied_margin_percent",
        "last_synced_at",
    )
    list_filter = ("source_name",)
    search_fields = ("external_product_id", "external_sku", "external_slug", "product__name", "product__sku")
    readonly_fields = ("created_at", "updated_at", "last_synced_at")


@admin.register(ImportJobLog)
class ImportJobLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_name",
        "source_type",
        "source_reference",
        "status",
        "is_dry_run",
        "total_records",
        "created_count",
        "updated_count",
        "duplicate_count",
        "error_count",
        "started_at",
        "finished_at",
    )
    list_filter = ("source_name", "source_type", "status", "is_dry_run")
    search_fields = ("source_reference", "id")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "row_errors",
        "row_warnings",
        "summary_data",
    )
