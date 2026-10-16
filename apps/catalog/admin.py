from django.contrib import admin
from .models import Brand, Category, Product, ProductImage, ProductReview, ProductVariant


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku_prefix", "category", "base_regular_price", "base_sale_price", "is_active", "is_development_fixture")
    list_filter = ("is_active", "is_featured", "is_new_arrival", "is_development_fixture", "category")
    search_fields = ("name", "sku_prefix")
    inlines = [ProductVariantInline, ProductImageInline]


admin.site.register(Category)
admin.site.register(Brand)
admin.site.register(ProductReview)
