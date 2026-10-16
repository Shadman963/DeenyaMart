"""Catalog services for safe inventory reservation/decrement rules and validated ORM query building."""
from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.db import transaction

from .forms import CatalogFilterForm
from .models import Category, Product, ProductVariant


class InventoryError(ValidationError):
    """Raised when an inventory reservation, release, or decrement violates stock rules."""


class InventoryService:
    """
    Centralized service enforcing safe quantity validation and atomic reservation/decrement rules.
    Never trusts client-submitted prices or unvalidated stock quantities.
    """

    @staticmethod
    def _validate_positive_quantity(quantity: int) -> int:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise InventoryError("Quantity must be a positive integer greater than zero.")
        return quantity

    @classmethod
    def reserve_variant_stock(cls, variant_id: int, quantity: int) -> ProductVariant:
        """Atomically locks and reserves `quantity` units on a `ProductVariant`."""
        qty = cls._validate_positive_quantity(quantity)
        with transaction.atomic():
            variant = (
                ProductVariant.objects.select_for_update()
                .select_related("product", "product__category")
                .filter(pk=variant_id)
                .first()
            )
            if not variant:
                raise InventoryError("Requested product variant does not exist.")
            if not variant.is_active or not variant.product.is_active or not variant.product.category.is_active:
                raise InventoryError(f"{variant.product.name} is inactive and cannot be reserved.")
            variant.reserve_stock(qty)
            return variant

    @classmethod
    def release_variant_stock(cls, variant_id: int, quantity: int) -> ProductVariant:
        """Atomically releases `quantity` units of reserved stock on a `ProductVariant`."""
        qty = cls._validate_positive_quantity(quantity)
        with transaction.atomic():
            variant = (
                ProductVariant.objects.select_for_update()
                .select_related("product")
                .filter(pk=variant_id)
                .first()
            )
            if not variant:
                raise InventoryError("Requested product variant does not exist.")
            variant.release_stock(qty)
            return variant

    @classmethod
    def decrement_variant_stock(
        cls,
        variant_id: int,
        quantity: int,
        from_reserved: bool = False,
    ) -> ProductVariant:
        """
        Atomically decrements `quantity` units of stock on a `ProductVariant`.
        If `from_reserved=True`, commits a prior reservation (decrementing both `stock_qty` and `reserved_qty`).
        Otherwise, verifies `available_stock >= quantity` and decrements `stock_qty`.
        """
        qty = cls._validate_positive_quantity(quantity)
        with transaction.atomic():
            variant = (
                ProductVariant.objects.select_for_update()
                .select_related("product", "product__category")
                .filter(pk=variant_id)
                .first()
            )
            if not variant:
                raise InventoryError("Requested product variant does not exist.")
            if not variant.is_active or not variant.product.is_active or not variant.product.category.is_active:
                raise InventoryError(f"{variant.product.name} is inactive and cannot be purchased.")
            variant.decrement_stock(qty, from_reserved=from_reserved)
            return variant


class CatalogQueryService:
    """Builds validated, N+1-free catalog querysets from untrusted request query parameters."""

    @classmethod
    def filter_products(cls, query_dict, base_category: Category | None = None):
        """
        Parses and validates `query_dict` via `CatalogFilterForm`, returning `(queryset, filters_meta)`.
        """
        params = CatalogFilterForm.parse_query_params(query_dict)
        qs = Product.objects.with_card_data().active()

        selected_category = base_category
        if base_category is not None:
            qs = qs.for_category(base_category)
        elif params["category"]:
            cat = Category.objects.active().filter(slug=params["category"]).first()
            if cat:
                selected_category = cat
                qs = qs.for_category(cat)
            else:
                # Non-existent or inactive category slug requested -> return empty queryset
                qs = qs.none()

        if params["brand"]:
            qs = qs.filter(brand__slug=params["brand"], brand__is_active=True)

        if params["min_price"] is not None or params["max_price"] is not None:
            qs = qs.price_range(min_price=params["min_price"], max_price=params["max_price"])

        if params["availability"] == "in_stock":
            qs = qs.in_stock()
        elif params["availability"] == "out_of_stock":
            qs = qs.out_of_stock()

        if params["on_sale"]:
            qs = qs.on_sale()

        if params["q"]:
            qs = qs.search(params["q"])

        qs = qs.sorted_by(params["sort"])

        # Build clean query string without `page` for pagination links
        query_items = {}
        if params["q"]:
            query_items["q"] = params["q"]
        if params["category"] and base_category is None:
            query_items["category"] = params["category"]
        if params["brand"]:
            query_items["fabric"] = params["brand"]
        if params["min_price"] is not None:
            query_items["min_price"] = str(params["min_price"])
        if params["max_price"] is not None:
            query_items["max_price"] = str(params["max_price"])
        if params["availability"] == "in_stock":
            query_items["in_stock"] = "1"
        elif params["availability"] == "out_of_stock":
            query_items["availability"] = "out_of_stock"
        if params["on_sale"]:
            query_items["on_sale"] = "1"
        if params["sort"] and params["sort"] != "featured":
            query_items["sort"] = params["sort"]

        params["selected_category"] = selected_category
        params["pagination_query_string"] = urlencode(query_items)
        return qs, params
