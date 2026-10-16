"""Django Admin configuration for catalog models, category hierarchy, inline images, and safe inventory editing."""
from django.contrib import admin
from django.db.models import Count

from .forms import ProductAdminForm, ProductVariantAdminForm
from .models import (
    Brand,
    Category,
    Product,
    ProductImage,
    ProductReview,
    ProductSource,
    ProductVariant,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "parent",
        "is_active",
        "is_featured",
        "sort_order",
        "product_count",
        "updated_at",
    )
    list_filter = ("is_active", "is_featured", "parent")
    search_fields = ("name", "slug", "subtitle", "description")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_active", "is_featured", "sort_order")
    ordering = ("sort_order", "name")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("parent").annotate(_product_count=Count("products"))

    @admin.display(description="Products", ordering="_product_count")
    def product_count(self, obj: Category) -> int:
        return getattr(obj, "_product_count", obj.products.count())


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "fabric_note", "is_featured", "is_active", "updated_at")
    list_filter = ("is_active", "is_featured")
    search_fields = ("name", "slug", "fabric_note")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "image_url", "alt_text", "display_order", "is_primary")
    ordering = ("-is_primary", "display_order", "id")


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    form = ProductVariantAdminForm
    extra = 1
    fields = (
        "sku",
        "size_label",
        "color_label",
        "option_name",
        "option_value",
        "price_override",
        "retail_price",
        "compare_at_price",
        "stock_qty",
        "reserved_qty",
        "available_stock",
        "low_stock_threshold",
        "is_active",
    )
    readonly_fields = ("available_stock",)


class ProductSourceInline(admin.TabularInline):
    model = ProductSource
    extra = 0
    fields = (
        "source_name",
        "external_id",
        "external_sku",
        "source_url",
        "sync_status",
        "last_synced_at",
        "is_development_fixture",
    )
    readonly_fields = ("last_synced_at",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = (
        "name",
        "sku",
        "category",
        "brand",
        "selling_price",
        "compare_at_price",
        "currency",
        "available_stock_display",
        "availability_state",
        "is_active",
        "is_featured",
        "is_development_fixture",
        "updated_at",
    )
    list_filter = (
        "is_active",
        "is_featured",
        "is_new_arrival",
        "is_hot_deal",
        "is_development_fixture",
        "track_inventory",
        "category",
        "brand",
    )
    search_fields = (
        "name",
        "slug",
        "sku",
        "sku_prefix",
        "short_description",
        "description",
        "variants__sku",
    )
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("uuid", "created_at", "updated_at")
    inlines = [ProductVariantInline, ProductImageInline, ProductSourceInline]
    actions = ["mark_active", "mark_inactive"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category", "brand")
            .prefetch_related("variants", "images")
        )

    @admin.display(description="Available Stock")
    def available_stock_display(self, obj: Product) -> int:
        return obj.available_stock

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance and form.instance.pk:
            form.instance.sync_stock_from_variants()

    @admin.action(description="Mark selected products as active")
    def mark_active(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Mark selected products as inactive")
    def mark_inactive(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    form = ProductVariantAdminForm
    list_display = (
        "sku",
        "product",
        "size_label",
        "color_label",
        "option_value",
        "effective_price",
        "compare_at_price",
        "stock_qty",
        "reserved_qty",
        "available_stock",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "product__category", "product__is_development_fixture")
    search_fields = (
        "sku",
        "product__name",
        "product__sku",
        "product__sku_prefix",
        "size_label",
        "color_label",
        "option_value",
    )
    readonly_fields = ("available_stock", "created_at", "updated_at")


@admin.register(ProductSource)
class ProductSourceAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "source_name",
        "external_id",
        "external_sku",
        "sync_status",
        "last_synced_at",
        "is_development_fixture",
    )
    list_filter = ("source_name", "sync_status", "is_development_fixture")
    search_fields = (
        "external_id",
        "external_sku",
        "external_slug",
        "source_url",
        "product__name",
        "product__sku",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "user",
        "rating",
        "is_verified_purchase",
        "is_approved",
        "created_at",
    )
    list_filter = ("is_approved", "is_verified_purchase", "rating")
    search_fields = ("product__name", "user__username", "comment")
    readonly_fields = ("created_at", "updated_at")
