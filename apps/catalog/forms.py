"""Forms for validating catalog filter and search query parameters and admin inventory inputs."""
from decimal import Decimal, InvalidOperation
from django import forms
from django.core.exceptions import ValidationError

from .models import Product, ProductVariant


class CatalogFilterForm(forms.Form):
    """
    Validates and normalizes untrusted query-string parameters for catalog listing,
    category pages, and search results.
    """

    SORT_CHOICES = [
        ("featured", "Featured Curations"),
        ("newest", "Newest Arrivals"),
        ("price_asc", "Price: Low to High"),
        ("price_desc", "Price: High to Low"),
        ("best_selling", "Best Selling"),
        ("name_asc", "Name: A to Z"),
        ("name_desc", "Name: Z to A"),
    ]
    VALID_SORT_KEYS = {k for k, _ in SORT_CHOICES} | {"price_low", "price_high", "popular", "name"}

    AVAILABILITY_CHOICES = [
        ("", "All Availability"),
        ("all", "All Availability"),
        ("in_stock", "In Stock Only"),
        ("out_of_stock", "Out of Stock"),
    ]

    q = forms.CharField(required=False, max_length=120)
    category = forms.SlugField(required=False, max_length=140)
    brand = forms.SlugField(required=False, max_length=120)
    fabric = forms.SlugField(required=False, max_length=120)
    min_price = forms.DecimalField(
        required=False,
        min_value=Decimal("0.00"),
        max_value=Decimal("9999999.99"),
        max_digits=10,
        decimal_places=2,
    )
    max_price = forms.DecimalField(
        required=False,
        min_value=Decimal("0.00"),
        max_value=Decimal("9999999.99"),
        max_digits=10,
        decimal_places=2,
    )
    availability = forms.ChoiceField(required=False, choices=AVAILABILITY_CHOICES)
    in_stock = forms.CharField(required=False, max_length=16)
    on_sale = forms.CharField(required=False, max_length=16)
    sort = forms.CharField(required=False, max_length=32)

    def clean_q(self) -> str:
        val = (self.cleaned_data.get("q") or "").replace("\x00", "").strip()
        return val[:120]

    def clean_sort(self) -> str:
        raw = (self.cleaned_data.get("sort") or "featured").strip().lower()
        if raw == "price_low":
            return "price_asc"
        if raw == "price_high":
            return "price_desc"
        if raw == "popular":
            return "best_selling"
        if raw == "name":
            return "name_asc"
        if raw in self.VALID_SORT_KEYS:
            return raw
        return "featured"

    @classmethod
    def parse_query_params(cls, query_dict) -> dict:
        """
        Safely extracts validated filter values from `request.GET`, ignoring malformed
        or out-of-range individual parameters without failing the whole page load.
        """
        form = cls(data=query_dict)
        form.is_valid()
        cleaned = getattr(form, "cleaned_data", {})

        # Extract individual fields safely, falling back to None/default if that field had errors
        def _safe_field(name, default=None):
            if name in form.errors:
                return default
            return cleaned.get(name, default)

        q = _safe_field("q", "") or ""
        if "q" in form.errors:
            raw_q = (query_dict.get("q") or "").replace("\x00", "").strip()
            q = raw_q[:120]

        category_slug = _safe_field("category", "") or ""
        brand_slug = _safe_field("fabric", "") or _safe_field("brand", "") or ""
        min_price = _safe_field("min_price", None)
        max_price = _safe_field("max_price", None)

        if min_price is not None and max_price is not None and min_price > max_price:
            min_price, max_price = max_price, min_price

        raw_avail = (_safe_field("availability", "") or "").strip().lower()
        raw_in_stock = (query_dict.get("in_stock") or "").strip().lower()
        if raw_avail in ("in_stock", "out_of_stock"):
            availability = raw_avail
        elif raw_in_stock in ("1", "true", "yes", "in_stock"):
            availability = "in_stock"
        elif raw_in_stock in ("0", "false", "out_of_stock"):
            availability = "out_of_stock"
        else:
            availability = ""

        raw_on_sale = (query_dict.get("on_sale") or "").strip().lower()
        on_sale = raw_on_sale in ("1", "true", "yes")

        # Always normalize sort safely
        raw_sort = (query_dict.get("sort") or "featured").strip().lower()
        if raw_sort == "price_low":
            sort = "price_asc"
        elif raw_sort == "price_high":
            sort = "price_desc"
        elif raw_sort == "popular":
            sort = "best_selling"
        elif raw_sort == "name":
            sort = "name_asc"
        elif raw_sort in cls.VALID_SORT_KEYS:
            sort = raw_sort
        else:
            sort = "featured"

        return {
            "q": q,
            "category": category_slug,
            "brand": brand_slug,
            "min_price": min_price,
            "max_price": max_price,
            "availability": availability,
            "in_stock": availability == "in_stock",
            "on_sale": on_sale,
            "sort": sort,
            "errors": dict(form.errors),
        }


class ProductAdminForm(forms.ModelForm):
    """Admin ModelForm enforcing safe price and inventory rules on Product."""

    class Meta:
        model = Product
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        stock_qty = cleaned_data.get("stock_qty") or 0
        reserved_qty = cleaned_data.get("reserved_qty") or 0
        if reserved_qty > stock_qty:
            self.add_error(
                "reserved_qty",
                "Reserved stock quantity cannot exceed on-hand stock quantity.",
            )
        selling_price = cleaned_data.get("selling_price")
        compare_at_price = cleaned_data.get("compare_at_price")
        if (
            selling_price is not None
            and compare_at_price is not None
            and compare_at_price < selling_price
        ):
            self.add_error(
                "compare_at_price",
                "Compare-at price must be greater than or equal to the selling price.",
            )
        return cleaned_data


class ProductVariantAdminForm(forms.ModelForm):
    """Admin ModelForm enforcing safe inventory editing and price override validation on ProductVariant."""

    class Meta:
        model = ProductVariant
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        stock_qty = cleaned_data.get("stock_qty") or 0
        reserved_qty = cleaned_data.get("reserved_qty") or 0
        if reserved_qty > stock_qty:
            self.add_error(
                "reserved_qty",
                f"Reserved quantity ({reserved_qty}) cannot exceed on-hand stock ({stock_qty}).",
            )
        price_override = cleaned_data.get("price_override")
        retail_price = cleaned_data.get("retail_price")
        compare_at_price = cleaned_data.get("compare_at_price")
        effective = price_override if price_override is not None else retail_price
        if (
            effective is not None
            and compare_at_price is not None
            and compare_at_price < effective
        ):
            self.add_error(
                "compare_at_price",
                "Compare-at price must be greater than or equal to the variant's selling price.",
            )
        return cleaned_data
