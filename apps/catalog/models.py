"""Catalog models: Category, Brand, Product, ProductVariant, ProductImage, ProductSource, ProductReview."""
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from apps.core.models import TimeStampedModel


def get_store_currency_code() -> str:
    """Returns the explicitly configured store currency code (initially BDT)."""
    return getattr(settings, "STORE_CURRENCY_CODE", "BDT")


def _generate_unique_slug(model_cls, base_text: str, pk=None, max_length: int = 140) -> str:
    """Generates a deterministic, unique slug for a model instance when slug is omitted."""
    base_slug = slugify(base_text or "")[: max_length - 8].strip("-") or "item"
    candidate = base_slug
    counter = 2
    qs = model_cls.objects.all()
    if pk is not None:
        qs = qs.exclude(pk=pk)
    while qs.filter(slug=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base_slug[: max_length - len(suffix)]}{suffix}"
        counter += 1
    return candidate


class CategoryQuerySet(models.QuerySet):
    """QuerySet helpers for Category hierarchy."""

    def active(self):
        return self.filter(is_active=True).filter(
            Q(parent__isnull=True) | Q(parent__is_active=True)
        )

    def top_level(self):
        return self.filter(parent__isnull=True)


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
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    subtitle = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to="categories/%Y/%m/",
        blank=True,
        null=True,
        help_text="Uploaded category image asset.",
    )
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

    objects = CategoryQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["parent", "is_active", "sort_order"]),
            models.Index(fields=["slug", "is_active"]),
        ]

    def __str__(self) -> str:
        if self.parent_id:
            return f"{self.parent.name} → {self.name}"
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("catalog:category_detail", kwargs={"slug": self.slug})

    @property
    def resolved_image_url(self) -> str:
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return self.image_url or "/static/img/categories/cat-womens-modest.svg"

    def clean(self):
        super().clean()
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "A category cannot be its own parent."})
        # Guard against deeper ancestor cycles
        ancestor = self.parent
        visited = {self.pk} if self.pk else set()
        while ancestor is not None:
            if ancestor.pk in visited:
                raise ValidationError({"parent": "Circular category hierarchy detected."})
            if ancestor.pk:
                visited.add(ancestor.pk)
            ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _generate_unique_slug(Category, self.name, pk=self.pk, max_length=140)
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "A category cannot be its own parent."})
        if self.image and not self.image_url:
            try:
                self.image_url = self.image.url
            except ValueError:
                pass
        super().save(*args, **kwargs)

    def get_descendant_ids(self, include_self: bool = True, active_only: bool = True) -> list[int]:
        """Returns IDs for this category and all nested subcategories."""
        if not self.pk:
            return []
        ids = [self.pk] if include_self else []
        frontier = [self.pk]
        while frontier:
            child_qs = Category.objects.filter(parent_id__in=frontier)
            if active_only:
                child_qs = child_qs.filter(is_active=True)
            child_ids = list(child_qs.values_list("id", flat=True))
            if not child_ids:
                break
            ids.extend(child_ids)
            frontier = child_ids
        return ids


class Brand(TimeStampedModel):
    """Brand or textile/fabric grouping (e.g., Pure Cotton, Natural Linen, Cherry Weave)."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    fabric_note = models.CharField(max_length=160, blank=True)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _generate_unique_slug(Brand, self.name, pk=self.pk, max_length=120)
        super().save(*args, **kwargs)


class ProductQuerySet(models.QuerySet):
    """Optimized queryset methods for catalog filtering, search, sorting, and N+1 prevention."""

    def active(self):
        """Returns active products belonging to active categories."""
        return self.filter(
            is_active=True,
            category__is_active=True,
        ).filter(
            Q(category__parent__isnull=True) | Q(category__parent__is_active=True)
        )

    def with_card_data(self):
        """Prefetches category, brand, images, and variants for zero-N+1 card rendering."""
        return self.select_related("category", "category__parent", "brand").prefetch_related(
            "images",
            "variants",
        )

    def in_stock(self):
        """Filters products that have available stock or do not track inventory."""
        return self.filter(
            Q(track_inventory=False)
            | Q(variants__is_active=True, variants__stock_qty__gt=F("variants__reserved_qty"))
            | Q(variants__isnull=True, stock_qty__gt=F("reserved_qty"))
        ).distinct()

    def out_of_stock(self):
        """Filters products that track inventory and have zero available stock."""
        in_stock_ids = self.in_stock().values_list("id", flat=True)
        return self.exclude(id__in=in_stock_ids)

    def on_sale(self):
        """Filters products with a genuine discount (compare_at_price > selling_price)."""
        return self.filter(
            compare_at_price__isnull=False,
            compare_at_price__gt=F("selling_price"),
            selling_price__gt=Decimal("0.00"),
        )

    def for_category(self, category: Category):
        """Filters products in the given category or any of its active descendant subcategories."""
        cat_ids = category.get_descendant_ids(include_self=True, active_only=True)
        return self.filter(category_id__in=cat_ids)

    def price_range(self, min_price: Decimal | None = None, max_price: Decimal | None = None):
        """Filters products by active selling price in store currency (BDT)."""
        qs = self
        if min_price is not None:
            qs = qs.filter(selling_price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(selling_price__lte=max_price)
        return qs

    def search(self, query: str):
        """Searches products by name, SKU, variant SKU, description, category, or brand."""
        q = (query or "").strip()
        if not q:
            return self
        return self.filter(
            Q(name__icontains=q)
            | Q(sku__icontains=q)
            | Q(sku_prefix__icontains=q)
            | Q(variants__sku__icontains=q)
            | Q(short_description__icontains=q)
            | Q(description__icontains=q)
            | Q(category__name__icontains=q)
            | Q(brand__name__icontains=q)
        ).distinct()

    def sorted_by(self, sort_key: str | None):
        """Applies validated catalog sorting."""
        key = (sort_key or "featured").strip().lower()
        if key == "newest":
            return self.order_by("-is_new_arrival", "-created_at", "id")
        if key in ("price_asc", "price_low"):
            return self.order_by("selling_price", "id")
        if key in ("price_desc", "price_high"):
            return self.order_by("-selling_price", "id")
        if key in ("name_asc", "name"):
            return self.order_by("name", "id")
        if key == "name_desc":
            return self.order_by("-name", "id")
        if key in ("best_selling", "popular"):
            return self.order_by("-total_sales", "sort_order", "-created_at", "id")
        return self.order_by("sort_order", "-is_featured", "-created_at", "id")


class Product(TimeStampedModel):
    """Core catalog product model with explicit BDT currency, genuine discount math, and stock tracking."""

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
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    sku = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        blank=True,
        help_text="Unique product SKU identifier.",
    )
    sku_prefix = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        blank=True,
        help_text="SKU prefix shared with product variants.",
    )

    currency = models.CharField(
        max_length=3,
        default=get_store_currency_code,
        help_text="ISO currency code for product prices (configured store currency, initially BDT).",
    )
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        db_index=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Active selling price in store currency (BDT).",
    )
    compare_at_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Optional original compare-at price in store currency (BDT).",
    )
    base_regular_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    base_sale_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    wholesale_cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    has_variants = models.BooleanField(default=False)
    track_inventory = models.BooleanField(
        default=True,
        help_text="Whether inventory quantities are tracked and enforced for purchases.",
    )
    stock_qty = models.PositiveIntegerField(
        default=0,
        help_text="On-hand stock quantity (automatically synced when active variants exist).",
    )
    reserved_qty = models.PositiveIntegerField(
        default=0,
        help_text="Reserved stock quantity held for pending orders.",
    )
    low_stock_threshold = models.PositiveSmallIntegerField(default=5)

    description = models.TextField(blank=True, help_text="Full plain-text or markdown description.")
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
        db_index=True,
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
                condition=Q(selling_price__gte=0),
                name="product_selling_price_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True) | Q(compare_at_price__gte=0),
                name="product_compare_at_price_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True)
                | Q(compare_at_price__gte=F("selling_price")),
                name="product_compare_at_gte_selling",
            ),
            models.CheckConstraint(
                condition=Q(base_regular_price__gte=0),
                name="product_regular_price_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(base_sale_price__isnull=True)
                | Q(base_sale_price__lte=F("base_regular_price")),
                name="product_sale_lte_regular",
            ),
            models.CheckConstraint(
                condition=Q(reserved_qty__lte=F("stock_qty")),
                name="product_reserved_lte_stock",
            ),
        ]
        indexes = [
            models.Index(fields=["category", "is_active", "-created_at"]),
            models.Index(fields=["is_active", "selling_price"]),
            models.Index(fields=["is_active", "is_featured", "sort_order"]),
            models.Index(fields=["is_active", "is_new_arrival", "-created_at"]),
        ]

    def __init__(self, *args, **kwargs):
        selling_given = "selling_price" in kwargs or "compare_at_price" in kwargs
        regular_given = "base_regular_price" in kwargs or "base_sale_price" in kwargs
        if "supplier_cost" in kwargs and "wholesale_cost_price" not in kwargs:
            kwargs["wholesale_cost_price"] = kwargs.pop("supplier_cost")
        if "stock_quantity" in kwargs and "stock_qty" not in kwargs:
            kwargs["stock_qty"] = kwargs.pop("stock_quantity")
        if "reserved_quantity" in kwargs and "reserved_qty" not in kwargs:
            kwargs["reserved_qty"] = kwargs.pop("reserved_quantity")
        super().__init__(*args, **kwargs)
        self._sync_identifiers_and_descriptions()
        if selling_given and not regular_given:
            self._sync_from_selling_price()
        elif regular_given and not selling_given:
            self._sync_from_regular_price()
        self._snapshot_price_state()

    def _snapshot_price_state(self):
        self._init_selling_price = self.selling_price
        self._init_compare_at_price = self.compare_at_price
        self._init_base_regular_price = self.base_regular_price
        self._init_base_sale_price = self.base_sale_price

    def _sync_identifiers_and_descriptions(self):
        if self.sku and not self.sku_prefix:
            self.sku_prefix = self.sku
        elif self.sku_prefix and not self.sku:
            self.sku = self.sku_prefix

        if self.description and not self.short_description:
            self.short_description = self.description[:280]
        elif self.short_description and not self.description:
            self.description = self.short_description

    def _sync_from_selling_price(self):
        if self.selling_price is not None:
            if (
                self.compare_at_price is not None
                and self.compare_at_price > self.selling_price
            ):
                self.base_regular_price = self.compare_at_price
                self.base_sale_price = self.selling_price
            elif self.compare_at_price is None or self.compare_at_price == self.selling_price:
                self.base_regular_price = self.selling_price
                self.base_sale_price = None

    def _sync_from_regular_price(self):
        if self.base_regular_price is not None:
            if (
                self.base_sale_price is not None
                and Decimal("0.00") < self.base_sale_price < self.base_regular_price
            ):
                self.selling_price = self.base_sale_price
                self.compare_at_price = self.base_regular_price
            elif self.base_sale_price is None or self.base_sale_price == self.base_regular_price:
                self.selling_price = self.base_regular_price
                self.compare_at_price = None
            elif self.base_sale_price <= Decimal("0.00") and self.base_sale_price == Decimal("0.00"):
                self.selling_price = self.base_regular_price
                self.compare_at_price = None

    def _reconcile_prices(self):
        selling_changed = (
            self.selling_price != getattr(self, "_init_selling_price", None)
            or self.compare_at_price != getattr(self, "_init_compare_at_price", None)
        )
        regular_changed = (
            self.base_regular_price != getattr(self, "_init_base_regular_price", None)
            or self.base_sale_price != getattr(self, "_init_base_sale_price", None)
        )
        if selling_changed and not regular_changed:
            self._sync_from_selling_price()
        elif regular_changed and not selling_changed:
            self._sync_from_regular_price()
        elif self.selling_price == Decimal("0.00") and self.base_regular_price > Decimal("0.00"):
            self._sync_from_regular_price()
        elif self.base_regular_price == Decimal("0.00") and self.selling_price > Decimal("0.00"):
            self._sync_from_selling_price()

    def __str__(self) -> str:
        return f"{self.name} ({self.sku or self.sku_prefix})"

    def get_absolute_url(self) -> str:
        return reverse("catalog:product_detail", kwargs={"slug": self.slug})

    @property
    def stock_quantity(self) -> int:
        return self.stock_qty

    @stock_quantity.setter
    def stock_quantity(self, value: int):
        self.stock_qty = value

    @property
    def reserved_quantity(self) -> int:
        return self.reserved_qty

    @reserved_quantity.setter
    def reserved_quantity(self, value: int):
        self.reserved_qty = value

    def clean(self):
        super().clean()
        self._sync_identifiers_and_descriptions()
        self._reconcile_prices()

        errors = {}
        if self.selling_price is not None and self.selling_price < Decimal("0.00"):
            errors["selling_price"] = "Selling price cannot be negative."
        if self.base_regular_price is not None and self.base_regular_price < Decimal("0.00"):
            errors["base_regular_price"] = "Regular price cannot be negative."
        if self.compare_at_price is not None:
            if self.compare_at_price < Decimal("0.00"):
                errors["compare_at_price"] = "Compare-at price cannot be negative."
            elif self.selling_price is not None and self.compare_at_price < self.selling_price:
                errors["compare_at_price"] = (
                    "Compare-at price must be greater than or equal to the selling price."
                )
        if self.base_sale_price is not None:
            if self.base_sale_price < Decimal("0.00"):
                errors["base_sale_price"] = "Sale price cannot be negative."
            elif self.base_regular_price is not None and self.base_sale_price > self.base_regular_price:
                errors["base_sale_price"] = "Sale price cannot exceed regular price."
        if self.wholesale_cost_price is not None and self.wholesale_cost_price < Decimal("0.00"):
            errors["wholesale_cost_price"] = "Wholesale cost price cannot be negative."
        if self.reserved_qty > self.stock_qty:
            errors["reserved_qty"] = "Reserved quantity cannot exceed on-hand stock quantity."
        if self.currency and len(self.currency.strip()) != 3:
            errors["currency"] = "Currency code must be a 3-letter ISO code (e.g., BDT)."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._sync_identifiers_and_descriptions()
        if not self.slug:
            self.slug = _generate_unique_slug(Product, self.name, pk=self.pk, max_length=280)
        if not self.sku and not self.sku_prefix:
            base_sku = slugify(self.name)[:24].upper().replace("-", "") or "ITEM"
            candidate = base_sku
            suffix = 1
            qs = Product.objects.all()
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            while qs.filter(Q(sku=candidate) | Q(sku_prefix=candidate)).exists():
                suffix += 1
                candidate = f"{base_sku}-{suffix}"
            self.sku = candidate
            self.sku_prefix = candidate
        if not self.currency:
            self.currency = get_store_currency_code()
        self.currency = self.currency.upper()
        self._reconcile_prices()
        super().save(*args, **kwargs)
        self._snapshot_price_state()

    @property
    def has_valid_discount(self) -> bool:
        """True only when a genuine discount exists (compare_at_price > selling_price > 0)."""
        if (
            self.compare_at_price is not None
            and self.selling_price is not None
            and self.selling_price > Decimal("0.00")
            and self.compare_at_price > self.selling_price
        ):
            return True
        return bool(
            self.base_sale_price is not None
            and self.base_sale_price > Decimal("0.00")
            and self.base_regular_price > Decimal("0.00")
            and self.base_sale_price < self.base_regular_price
        )

    @property
    def current_price(self) -> Decimal:
        """Active selling price in store currency (BDT)."""
        if self.selling_price and self.selling_price > Decimal("0.00"):
            return self.selling_price
        if self.has_valid_discount and self.base_sale_price is not None:
            return self.base_sale_price
        return self.base_regular_price

    @property
    def original_price(self) -> Decimal | None:
        """Original compare-at price, returned only when a valid discount exists."""
        if not self.has_valid_discount:
            return None
        if self.compare_at_price and self.compare_at_price > self.current_price:
            return self.compare_at_price
        return self.base_regular_price

    @property
    def discount_percentage(self) -> int:
        """Genuine percentage discount calculation."""
        orig = self.original_price
        curr = self.current_price
        if not orig or orig <= Decimal("0.00") or curr >= orig:
            return 0
        ratio = ((orig - curr) / orig) * Decimal("100")
        return int(round(ratio))

    @property
    def discount_savings(self) -> Decimal:
        """Exact monetary amount saved in store currency (BDT)."""
        orig = self.original_price
        curr = self.current_price
        if not orig or curr >= orig:
            return Decimal("0.00")
        return orig - curr

    @property
    def supplier_cost(self) -> Decimal | None:
        """Supplier wholesale cost in store currency (BDT), kept separate from retail selling price."""
        return self.wholesale_cost_price

    @supplier_cost.setter
    def supplier_cost(self, value: Decimal | None):
        self.wholesale_cost_price = value

    @property
    def margin_amount(self) -> Decimal | None:
        """Unit retail gross margin (selling_price - supplier_cost) when supplier cost is known."""
        if self.wholesale_cost_price is None or self.current_price is None:
            return None
        return self.current_price - self.wholesale_cost_price

    @property
    def margin_percentage(self) -> Decimal | None:
        """Gross margin percentage ((selling_price - supplier_cost) / selling_price * 100)."""
        if (
            self.wholesale_cost_price is None
            or not self.current_price
            or self.current_price <= Decimal("0.00")
        ):
            return None
        margin_pct = ((self.current_price - self.wholesale_cost_price) / self.current_price) * Decimal("100")
        return margin_pct.quantize(Decimal("0.01"))

    @property
    def markup_percentage(self) -> Decimal | None:
        """Markup percentage over supplier cost ((selling_price - supplier_cost) / supplier_cost * 100)."""
        if (
            not self.wholesale_cost_price
            or self.wholesale_cost_price <= Decimal("0.00")
            or self.current_price is None
        ):
            return None
        markup_pct = ((self.current_price - self.wholesale_cost_price) / self.wholesale_cost_price) * Decimal("100")
        return markup_pct.quantize(Decimal("0.01"))

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
            if v.available_stock > 0 or not self.track_inventory:
                return v
        return variants[0] if variants else None

    @property
    def available_stock(self) -> int:
        """Total available (unreserved) stock across active variants or product-level stock."""
        variants = self._active_variants()
        if variants:
            return sum(v.available_stock for v in variants)
        if self.pk and self.variants.exists():
            # Product has variants, but all of them are inactive
            return 0
        return max(0, self.stock_qty - self.reserved_qty)

    @property
    def total_stock(self) -> int:
        """Alias for available_stock used by templates and storefront cards."""
        return self.available_stock

    @property
    def is_in_stock(self) -> bool:
        """True only when product (and its category) is active and stock is available."""
        if not self.is_active:
            return False
        if self.category_id and not self.category.is_active:
            return False
        if not self.track_inventory:
            variants = self._active_variants()
            if self.pk and self.variants.exists() and not variants:
                return False
            return True
        return self.available_stock > 0

    @property
    def availability_state(self) -> str:
        if not self.is_active:
            return "out_of_stock"
        if not self.track_inventory:
            return "in_stock"
        stock = self.available_stock
        if stock <= 0:
            return "out_of_stock"
        if stock <= self.low_stock_threshold:
            return "low_stock"
        return "in_stock"

    @property
    def availability_label(self) -> str:
        if not self.is_active:
            return "Unavailable"
        if not self.track_inventory:
            return "In Stock"
        stock = self.available_stock
        if stock <= 0:
            return "Out of Stock"
        if stock <= self.low_stock_threshold:
            return f"Low Stock ({stock} left)"
        return "In Stock"

    def sync_stock_from_variants(self):
        """Synchronizes product-level stock_qty and reserved_qty from active variants."""
        if not self.pk:
            return
        agg = self.variants.filter(is_active=True).aggregate(
            total_stock=Sum("stock_qty"),
            total_reserved=Sum("reserved_qty"),
        )
        new_stock = agg["total_stock"] or 0
        new_reserved = agg["total_reserved"] or 0
        self.stock_qty = new_stock
        self.reserved_qty = new_reserved
        Product.objects.filter(pk=self.pk).update(
            stock_qty=new_stock,
            reserved_qty=new_reserved,
        )

    def reserve_stock(self, quantity: int) -> int:
        """Reserves stock on the default variant (or product directly if no variants exist)."""
        if quantity <= 0:
            raise ValidationError("Reservation quantity must be a positive integer.")
        if not self.is_active:
            raise ValidationError("Cannot reserve stock for an inactive product.")
        variant = self.default_variant
        if variant:
            return variant.reserve_stock(quantity)
        with transaction.atomic():
            locked = Product.objects.select_for_update().get(pk=self.pk)
            if not locked.is_active:
                raise ValidationError("Cannot reserve stock for an inactive product.")
            avail = max(0, locked.stock_qty - locked.reserved_qty)
            if locked.track_inventory and avail < quantity:
                raise ValidationError(
                    f"Insufficient available stock for {locked.name}. Requested {quantity}, available {avail}."
                )
            locked.reserved_qty += quantity
            locked.save(update_fields=["reserved_qty", "updated_at"])
            self.reserved_qty = locked.reserved_qty
            return locked.available_stock

    def release_stock(self, quantity: int) -> int:
        """Releases previously reserved stock on the default variant or product."""
        if quantity <= 0:
            raise ValidationError("Release quantity must be a positive integer.")
        variant = self.default_variant
        if variant and variant.reserved_qty >= quantity:
            return variant.release_stock(quantity)
        with transaction.atomic():
            locked = Product.objects.select_for_update().get(pk=self.pk)
            if locked.reserved_qty < quantity:
                raise ValidationError(
                    f"Cannot release {quantity} units; only {locked.reserved_qty} reserved."
                )
            locked.reserved_qty -= quantity
            locked.save(update_fields=["reserved_qty", "updated_at"])
            self.reserved_qty = locked.reserved_qty
            return locked.available_stock

    def decrement_stock(self, quantity: int, from_reserved: bool = False) -> int:
        """Decrements on-hand stock safely on the default variant or product."""
        if quantity <= 0:
            raise ValidationError("Decrement quantity must be a positive integer.")
        if not self.is_active:
            raise ValidationError("Cannot decrement stock for an inactive product.")
        variant = self.default_variant
        if variant:
            return variant.decrement_stock(quantity, from_reserved=from_reserved)
        with transaction.atomic():
            locked = Product.objects.select_for_update().get(pk=self.pk)
            if not locked.is_active:
                raise ValidationError("Cannot decrement stock for an inactive product.")
            if from_reserved:
                if locked.reserved_qty < quantity or locked.stock_qty < quantity:
                    raise ValidationError("Insufficient reserved stock to commit decrement.")
                locked.stock_qty -= quantity
                locked.reserved_qty -= quantity
            else:
                avail = max(0, locked.stock_qty - locked.reserved_qty)
                if locked.track_inventory and avail < quantity:
                    raise ValidationError(
                        f"Insufficient available stock for {locked.name}. Requested {quantity}, available {avail}."
                    )
                locked.stock_qty = max(0, locked.stock_qty - quantity)
            locked.save(update_fields=["stock_qty", "reserved_qty", "updated_at"])
            self.stock_qty = locked.stock_qty
            self.reserved_qty = locked.reserved_qty
            return locked.available_stock


class ProductImage(TimeStampedModel):
    """Primary and secondary gallery images for a product."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(
        upload_to="products/%Y/%m/",
        blank=True,
        null=True,
        help_text="Uploaded product image file.",
    )
    image_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Static or media URL path to the product image.",
    )
    alt_text = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveSmallIntegerField(
        default=0,
        db_index=True,
        help_text="Display order in product image gallery (lower numbers appear first).",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-is_primary", "display_order", "sort_order", "id"]
        indexes = [
            models.Index(fields=["product", "-is_primary", "display_order"]),
        ]

    def __init__(self, *args, **kwargs):
        display_given = "display_order" in kwargs
        sort_given = "sort_order" in kwargs
        super().__init__(*args, **kwargs)
        if display_given and not sort_given:
            self.sort_order = self.display_order
        elif sort_given and not display_given:
            self.display_order = self.sort_order

    def __str__(self) -> str:
        return f"Image for {self.product.name} ({'Primary' if self.is_primary else 'Secondary'})"

    @property
    def resolved_url(self) -> str:
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return self.image_url or "/static/img/fixtures/lf104-primary.svg"

    def clean(self):
        super().clean()
        if not self.image and not self.image_url:
            raise ValidationError("Either an uploaded image or an image_url must be provided.")

    def save(self, *args, **kwargs):
        if self.display_order != self.sort_order:
            if self.display_order != 0:
                self.sort_order = self.display_order
            else:
                self.display_order = self.sort_order
        if self.image and not self.image_url:
            try:
                self.image_url = self.image.url
            except ValueError:
                pass
        if not self.alt_text and self.product_id:
            self.alt_text = self.product.name
        super().save(*args, **kwargs)
        if self.is_primary and self.product_id:
            ProductImage.objects.filter(product_id=self.product_id, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)


class ProductVariant(TimeStampedModel):
    """Concrete purchasable SKU with size/color/custom options, price overrides, and stock reservation."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    sku = models.CharField(max_length=80, unique=True, db_index=True)
    size_label = models.CharField(max_length=50, blank=True, help_text="Selectable size option (e.g., M, Free Size, Age 7-8).")
    color_label = models.CharField(max_length=50, blank=True, help_text="Selectable color option (e.g., Deep Cypress, Warm Sand).")
    color_hex = models.CharField(max_length=7, blank=True)
    option_name = models.CharField(
        max_length=50,
        blank=True,
        help_text="Additional selectable option name (e.g., Material, Length, Pack Size).",
    )
    option_value = models.CharField(
        max_length=80,
        blank=True,
        help_text="Additional selectable option value.",
    )
    price_override = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Optional variant-specific selling price override in store currency (BDT).",
    )
    retail_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Effective retail selling price in BDT (auto-populated from product or price_override).",
    )
    compare_at_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    wholesale_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    stock_qty = models.PositiveIntegerField(default=0)
    reserved_qty = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveSmallIntegerField(default=5)
    image = models.ForeignKey(
        ProductImage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="variants",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price_override__isnull=True) | Q(price_override__gte=0),
                name="variant_price_override_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(retail_price__isnull=True) | Q(retail_price__gte=0),
                name="variant_retail_price_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True) | Q(compare_at_price__gte=0),
                name="variant_compare_at_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True)
                | Q(retail_price__isnull=True)
                | Q(compare_at_price__gte=F("retail_price")),
                name="variant_compare_at_gte_retail",
            ),
            models.CheckConstraint(
                condition=Q(reserved_qty__lte=F("stock_qty")),
                name="variant_reserved_lte_stock",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "is_active", "stock_qty"]),
        ]

    def __init__(self, *args, **kwargs):
        if "size" in kwargs and "size_label" not in kwargs:
            kwargs["size_label"] = kwargs.pop("size")
        if "color" in kwargs and "color_label" not in kwargs:
            kwargs["color_label"] = kwargs.pop("color")
        if "supplier_cost" in kwargs and "wholesale_price" not in kwargs:
            kwargs["wholesale_price"] = kwargs.pop("supplier_cost")
        if "stock_quantity" in kwargs and "stock_qty" not in kwargs:
            kwargs["stock_qty"] = kwargs.pop("stock_quantity")
        if "reserved_quantity" in kwargs and "reserved_qty" not in kwargs:
            kwargs["reserved_qty"] = kwargs.pop("reserved_quantity")
        super().__init__(*args, **kwargs)

    def __str__(self) -> str:
        parts = [p for p in (self.size_label, self.color_label, self.option_value) if p]
        label = " / ".join(parts) if parts else "Standard"
        return f"{self.product.name} — {label} ({self.sku})"

    @property
    def supplier_cost(self) -> Decimal | None:
        return self.wholesale_price

    @supplier_cost.setter
    def supplier_cost(self, value: Decimal | None):
        self.wholesale_price = value

    @property
    def margin_amount(self) -> Decimal | None:
        if self.wholesale_price is None or self.effective_price is None:
            return None
        return self.effective_price - self.wholesale_price

    @property
    def margin_percentage(self) -> Decimal | None:
        if (
            self.wholesale_price is None
            or not self.effective_price
            or self.effective_price <= Decimal("0.00")
        ):
            return None
        return (((self.effective_price - self.wholesale_price) / self.effective_price) * Decimal("100")).quantize(
            Decimal("0.01")
        )

    @property
    def size(self) -> str:
        return self.size_label

    @size.setter
    def size(self, value: str):
        self.size_label = value

    @property
    def color(self) -> str:
        return self.color_label

    @color.setter
    def color(self, value: str):
        self.color_label = value

    @property
    def stock_quantity(self) -> int:
        return self.stock_qty

    @stock_quantity.setter
    def stock_quantity(self, value: int):
        self.stock_qty = value

    @property
    def reserved_quantity(self) -> int:
        return self.reserved_qty

    @reserved_quantity.setter
    def reserved_quantity(self, value: int):
        self.reserved_qty = value

    @property
    def display_label(self) -> str:
        parts = [p for p in (self.size_label, self.color_label) if p]
        if self.option_value:
            parts.append(
                f"{self.option_name}: {self.option_value}" if self.option_name else self.option_value
            )
        return " · ".join(parts) if parts else "Standard Size"

    @property
    def effective_price(self) -> Decimal:
        """Returns variant price_override if set, else retail_price, else parent product's current_price."""
        if self.price_override is not None:
            return self.price_override
        if self.retail_price is not None:
            return self.retail_price
        if self.product_id:
            return self.product.current_price
        return Decimal("0.00")

    @property
    def selling_price(self) -> Decimal:
        return self.effective_price

    @property
    def available_stock(self) -> int:
        """Unreserved purchasable stock for this variant."""
        return max(0, self.stock_qty - self.reserved_qty)

    @property
    def is_in_stock(self) -> bool:
        if not self.is_active or (self.product_id and not self.product.is_active):
            return False
        if self.product_id and not self.product.track_inventory:
            return True
        return self.available_stock > 0

    def clean(self):
        super().clean()
        errors = {}
        if self.price_override is not None and self.price_override < Decimal("0.00"):
            errors["price_override"] = "Price override cannot be negative."
        if self.retail_price is not None and self.retail_price < Decimal("0.00"):
            errors["retail_price"] = "Retail price cannot be negative."
        eff_price = (
            self.price_override
            if self.price_override is not None
            else self.retail_price
        )
        if self.compare_at_price is not None:
            if self.compare_at_price < Decimal("0.00"):
                errors["compare_at_price"] = "Compare-at price cannot be negative."
            elif eff_price is not None and self.compare_at_price < eff_price:
                errors["compare_at_price"] = (
                    "Compare-at price must be greater than or equal to the variant selling price."
                )
        if self.reserved_qty > self.stock_qty:
            errors["reserved_qty"] = "Reserved stock cannot exceed on-hand stock quantity."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.price_override is not None:
            self.retail_price = self.price_override
        elif self.retail_price is None and self.product_id:
            self.retail_price = self.product.current_price
        super().save(*args, **kwargs)
        if self.product_id:
            self.product.sync_stock_from_variants()

    def delete(self, *args, **kwargs):
        product = self.product if self.product_id else None
        res = super().delete(*args, **kwargs)
        if product and product.pk:
            product.sync_stock_from_variants()
        return res

    def reserve_stock(self, quantity: int) -> int:
        """Atomically validates and reserves `quantity` units of stock for this variant."""
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("Reservation quantity must be a positive integer.")
        with transaction.atomic():
            locked = (
                ProductVariant.objects.select_for_update()
                .select_related("product")
                .get(pk=self.pk)
            )
            if not locked.is_active or not locked.product.is_active:
                raise ValidationError("Cannot reserve stock for an inactive product or variant.")
            avail = locked.available_stock
            if locked.product.track_inventory and avail < quantity:
                raise ValidationError(
                    f"Insufficient available stock for {locked.sku}. Requested {quantity}, available {avail}."
                )
            locked.reserved_qty += quantity
            locked.save(update_fields=["reserved_qty", "updated_at"])
            locked.product.sync_stock_from_variants()
            self.reserved_qty = locked.reserved_qty
            return locked.available_stock

    def release_stock(self, quantity: int) -> int:
        """Atomically releases `quantity` units of previously reserved stock."""
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("Release quantity must be a positive integer.")
        with transaction.atomic():
            locked = (
                ProductVariant.objects.select_for_update()
                .select_related("product")
                .get(pk=self.pk)
            )
            if locked.reserved_qty < quantity:
                raise ValidationError(
                    f"Cannot release {quantity} units for {locked.sku}; only {locked.reserved_qty} reserved."
                )
            locked.reserved_qty -= quantity
            locked.save(update_fields=["reserved_qty", "updated_at"])
            locked.product.sync_stock_from_variants()
            self.reserved_qty = locked.reserved_qty
            return locked.available_stock

    def decrement_stock(self, quantity: int, from_reserved: bool = False) -> int:
        """
        Atomically decrements stock_qty by `quantity`.
        - When `from_reserved=True`, commits a prior reservation by decrementing both `stock_qty` and `reserved_qty`.
        - When `from_reserved=False`, verifies unreserved `available_stock >= quantity` and decrements `stock_qty`.
        """
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("Decrement quantity must be a positive integer.")
        with transaction.atomic():
            locked = (
                ProductVariant.objects.select_for_update()
                .select_related("product")
                .get(pk=self.pk)
            )
            if not locked.is_active or not locked.product.is_active:
                raise ValidationError("Cannot purchase or decrement stock for an inactive product or variant.")
            if from_reserved:
                if locked.reserved_qty < quantity or locked.stock_qty < quantity:
                    raise ValidationError(
                        f"Insufficient reserved stock for {locked.sku} (reserved: {locked.reserved_qty}, requested: {quantity})."
                    )
                locked.stock_qty -= quantity
                locked.reserved_qty -= quantity
            else:
                avail = locked.available_stock
                if locked.product.track_inventory and avail < quantity:
                    raise ValidationError(
                        f"Insufficient available stock for {locked.sku}. Requested {quantity}, available {avail}."
                    )
                if locked.stock_qty < quantity:
                    raise ValidationError(
                        f"Insufficient on-hand stock for {locked.sku}. Requested {quantity}, on-hand {locked.stock_qty}."
                    )
                locked.stock_qty -= quantity
            locked.save(update_fields=["stock_qty", "reserved_qty", "updated_at"])
            locked.product.sync_stock_from_variants()
            self.stock_qty = locked.stock_qty
            self.reserved_qty = locked.reserved_qty
            return locked.available_stock

    def restock(self, quantity: int) -> int:
        """Atomically adds `quantity` units to on-hand stock."""
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("Restock quantity must be a positive integer.")
        with transaction.atomic():
            locked = (
                ProductVariant.objects.select_for_update()
                .select_related("product")
                .get(pk=self.pk)
            )
            locked.stock_qty += quantity
            locked.save(update_fields=["stock_qty", "updated_at"])
            locked.product.sync_stock_from_variants()
            self.stock_qty = locked.stock_qty
            return locked.available_stock


class ProductSource(TimeStampedModel):
    """Tracks external catalog source identifiers, source URL, last synchronization time, and status."""

    class SyncStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SYNCED = "SYNCED", "Synced"
        FAILED = "FAILED", "Failed"
        OUTDATED = "OUTDATED", "Outdated"
        DISABLED = "DISABLED", "Disabled"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    source_name = models.CharField(
        max_length=64,
        default="droploo",
        db_index=True,
        help_text="External supplier or feed system name (e.g., droploo).",
    )
    external_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="External product identifier on the source platform.",
    )
    external_sku = models.CharField(
        max_length=128,
        blank=True,
        help_text="External SKU or product code in the supplier system.",
    )
    external_slug = models.CharField(max_length=280, blank=True)
    source_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Direct URL to the product or feed endpoint on the external source.",
    )
    sync_status = models.CharField(
        max_length=32,
        choices=SyncStatus.choices,
        default=SyncStatus.PENDING,
        db_index=True,
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp of the most recent synchronization.",
    )
    sync_error_message = models.TextField(
        blank=True,
        help_text="Error message recorded if the last sync failed.",
    )
    raw_payload_hash = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA-256 digest of the external product payload for delta detection.",
    )
    raw_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of external supplier metadata.",
    )
    is_development_fixture = models.BooleanField(
        default=False,
        help_text="True when this source mapping is sample development data.",
    )

    class Meta:
        ordering = ["-last_synced_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_name", "external_id"],
                name="uniq_product_source_external_id",
            ),
        ]
        indexes = [
            models.Index(fields=["source_name", "sync_status"]),
            models.Index(fields=["product", "source_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_name}:{self.external_id} → {self.product.name} [{self.sync_status}]"

    def mark_synced(self, payload_hash: str = "", synced_at=None):
        """Marks this source record as successfully synchronized."""
        self.sync_status = self.SyncStatus.SYNCED
        self.last_synced_at = synced_at or timezone.now()
        self.sync_error_message = ""
        if payload_hash:
            self.raw_payload_hash = payload_hash
        self.save(
            update_fields=[
                "sync_status",
                "last_synced_at",
                "sync_error_message",
                "raw_payload_hash",
                "updated_at",
            ]
        )

    def mark_failed(self, error_message: str):
        """Marks this source record as failed with an error message."""
        self.sync_status = self.SyncStatus.FAILED
        self.sync_error_message = error_message
        self.save(update_fields=["sync_status", "sync_error_message", "updated_at"])


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
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField()
    is_verified_purchase = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=1) & Q(rating__lte=5),
                name="review_rating_between_1_and_5",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.rating}★ review for {self.product.name}"
