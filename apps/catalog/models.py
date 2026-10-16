"""Catalog models: Category, Brand, Product, ProductVariant, ProductImage, ProductReview."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from apps.core.models import TimeStampedModel


class Category(TimeStampedModel):
    """Self-referencing product category hierarchy."""

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    subtitle = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    image_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="Static or media URL for category editorial artwork.",
    )
    accent_tone = models.CharField(
        max_length=32,
        default="emerald",
        help_text="Design token theme variant (emerald, terracotta, sand, brass).",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)
    seo_title = models.CharField(max_length=160, blank=True)
    seo_description = models.CharField(max_length=280, blank=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["parent", "is_active", "sort_order"]),
        ]

    def __str__(self) -> str:
        if self.parent_id:
            return f"{self.parent.name} → {self.name}"
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("catalog:category_detail", kwargs={"slug": self.slug})


class Brand(TimeStampedModel):
    """Brand or textile/fabric grouping (e.g., Pure Cotton, Natural Linen, Cherry Weave)."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    fabric_note = models.CharField(max_length=160, blank=True)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ProductQuerySet(models.QuerySet):
    """Optimized queryset methods preventing N+1 queries on product cards."""

    def active(self):
        return self.filter(is_active=True)

    def with_card_data(self):
        return self.select_related("category", "brand").prefetch_related(
            "images",
            "variants",
        )


class Product(TimeStampedModel):
    """Core catalog product model with genuine discount math and stock introspection."""

    IMAGE_FIT_CHOICES = [
        ("cover", "Cover (Editorial 4:5 Full Frame)"),
        ("contain", "Contain (Technical / Studio Centered)"),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True)
    sku_prefix = models.CharField(max_length=64, unique=True, db_index=True)

    has_variants = models.BooleanField(default=False)
    base_regular_price = models.DecimalField(max_digits=10, decimal_places=2)
    base_sale_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    wholesale_cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    short_description = models.TextField(blank=True)
    description_html = models.TextField(blank=True)
    fabric_composition = models.CharField(max_length=120, blank=True)
    unit = models.CharField(max_length=20, default="PC")
    weight_grams = models.PositiveIntegerField(default=300)
    image_fit = models.CharField(
        max_length=16,
        choices=IMAGE_FIT_CHOICES,
        default="cover",
    )

    inside_dhaka_shipping_override = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    outside_dhaka_shipping_override = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )

    is_featured = models.BooleanField(default=False, db_index=True)
    is_new_arrival = models.BooleanField(default=False, db_index=True)
    is_hot_deal = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_development_fixture = models.BooleanField(
        default=False,
        help_text="True when this record is a development sample fixture awaiting live catalog import.",
    )

    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    rating_count = models.PositiveIntegerField(default=0)
    total_sales = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(base_regular_price__gte=0),
                name="product_regular_price_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(base_sale_price__isnull=True)
                | Q(base_sale_price__lte=F("base_regular_price")),
                name="product_sale_lte_regular",
            ),
        ]
        indexes = [
            models.Index(fields=["category", "is_active", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku_prefix})"

    def get_absolute_url(self) -> str:
        return reverse("catalog:product_detail", kwargs={"slug": self.slug})

    @property
    def has_valid_discount(self) -> bool:
        """True only when base_sale_price is positive and strictly less than base_regular_price."""
        return bool(
            self.base_sale_price is not None
            and self.base_sale_price > Decimal("0.00")
            and self.base_regular_price > Decimal("0.00")
            and self.base_sale_price < self.base_regular_price
        )

    @property
    def current_price(self) -> Decimal:
        """Active selling price."""
        if self.has_valid_discount:
            return self.base_sale_price
        return self.base_regular_price

    @property
    def original_price(self):
        """Original compare-at price, returned only when a valid discount exists."""
        if self.has_valid_discount:
            return self.base_regular_price
        return None

    @property
    def discount_percentage(self) -> int:
        """Genuine percentage discount calculation."""
        if not self.has_valid_discount:
            return 0
        ratio = (
            (self.base_regular_price - self.base_sale_price)
            / self.base_regular_price
        ) * Decimal("100")
        return int(round(ratio))

    @property
    def discount_savings(self) -> Decimal:
        """Exact monetary amount saved."""
        if not self.has_valid_discount:
            return Decimal("0.00")
        return self.base_regular_price - self.base_sale_price

    def _prefetched_images(self):
        return list(self.images.all())

    @property
    def primary_image(self):
        imgs = self._prefetched_images()
        for img in imgs:
            if img.is_primary:
                return img
        return imgs[0] if imgs else None

    @property
    def secondary_image(self):
        imgs = self._prefetched_images()
        primary = self.primary_image
        for img in imgs:
            if primary and img.pk != primary.pk:
                return img
        return None

    def _active_variants(self):
        return [v for v in self.variants.all() if v.is_active]

    @property
    def default_variant(self):
        variants = self._active_variants()
        for v in variants:
            if v.stock_qty > 0:
                return v
        return variants[0] if variants else None

    @property
    def total_stock(self) -> int:
        return sum(v.stock_qty for v in self._active_variants())

    @property
    def is_in_stock(self) -> bool:
        return self.total_stock > 0

    @property
    def availability_state(self) -> str:
        stock = self.total_stock
        if stock <= 0:
            return "out_of_stock"
        if stock <= 5:
            return "low_stock"
        return "in_stock"

    @property
    def availability_label(self) -> str:
        stock = self.total_stock
        if stock <= 0:
            return "Out of Stock"
        if stock <= 5:
            return f"Low Stock ({stock} left)"
        return "In Stock"


class ProductVariant(TimeStampedModel):
    """Concrete purchasable SKU with size/color attributes and stock level."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    sku = models.CharField(max_length=80, unique=True, db_index=True)
    size_label = models.CharField(max_length=50, blank=True)
    color_label = models.CharField(max_length=50, blank=True)
    color_hex = models.CharField(max_length=7, blank=True)
    retail_price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    wholesale_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    stock_qty = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        parts = [p for p in (self.size_label, self.color_label) if p]
        label = " / ".join(parts) if parts else "Standard"
        return f"{self.product.name} — {label} ({self.sku})"

    @property
    def display_label(self) -> str:
        parts = [p for p in (self.size_label, self.color_label) if p]
        return " · ".join(parts) if parts else "Standard Size"


class ProductImage(TimeStampedModel):
    """Primary and secondary gallery images for a product."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image_url = models.CharField(
        max_length=500,
        help_text="Static or media URL path to the product image.",
    )
    alt_text = models.CharField(max_length=255)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-is_primary", "sort_order", "id"]

    def __str__(self) -> str:
        return f"Image for {self.product.name} ({'Primary' if self.is_primary else 'Secondary'})"


class ProductReview(TimeStampedModel):
    """Verified customer reviews (never fabricated)."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_reviews",
    )
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField()
    is_verified_purchase = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
