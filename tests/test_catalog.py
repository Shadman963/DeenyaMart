"""Comprehensive automated tests for the DeenyaMart Django catalog backend."""
from decimal import Decimal
from django.conf import settings
from django.contrib.admin.sites import site as admin_site
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.catalog.forms import CatalogFilterForm, ProductAdminForm, ProductVariantAdminForm
from apps.catalog.models import (
    Brand,
    Category,
    Product,
    ProductImage,
    ProductReview,
    ProductSource,
    ProductVariant,
)
from apps.catalog.services import InventoryError, InventoryService
from apps.shipping.models import ShippingZone


class CatalogModelAndRelationshipTests(TestCase):
    """Tests slugs, category hierarchy, prices, currency, images, variants, and ProductSource."""

    def setUp(self):
        self.parent_cat = Category.objects.create(
            name="Modest Outerwear",
            description="Parent category for modest outerwear.",
        )
        self.child_cat = Category.objects.create(
            name="Linen Abayas",
            parent=self.parent_cat,
            description="Breathable linen abayas.",
        )

    def test_explicit_store_currency_configuration(self):
        """Verifies BDT store currency configuration in settings and Product defaults."""
        self.assertEqual(settings.STORE_CURRENCY_CODE, "BDT")
        self.assertEqual(settings.STORE_CURRENCY_SYMBOL, "৳")

        product = Product.objects.create(
            name="Classic Linen Abaya",
            sku="CLA-001",
            category=self.child_cat,
            selling_price=Decimal("1550.00"),
        )
        self.assertEqual(product.currency, "BDT")
        self.assertIsInstance(product.selling_price, Decimal)

    def test_category_and_product_slug_generation_and_uniqueness(self):
        """Verifies automatic slug generation, collision handling, and uniqueness constraints."""
        self.assertEqual(self.parent_cat.slug, "modest-outerwear")
        self.assertEqual(self.child_cat.slug, "linen-abayas")

        # Auto-generated slug resolves collisions deterministically when slug is omitted
        duplicate_name_cat = Category.objects.create(name="Modest Outerwear")
        self.assertEqual(duplicate_name_cat.slug, "modest-outerwear-2")

        # Explicit duplicate slug violates uniqueness constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(name="Another Cat", slug="modest-outerwear")

        p1 = Product.objects.create(
            name="Signature Cotton Panjabi",
            sku="SCP-101",
            category=self.child_cat,
            selling_price=Decimal("1200.00"),
        )
        p2 = Product.objects.create(
            name="Signature Cotton Panjabi",
            sku="SCP-102",
            category=self.child_cat,
            selling_price=Decimal("1250.00"),
        )
        self.assertEqual(p1.slug, "signature-cotton-panjabi")
        self.assertEqual(p2.slug, "signature-cotton-panjabi-2")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(
                    name="Duplicate Slug Product",
                    slug="signature-cotton-panjabi",
                    sku="SCP-103",
                    category=self.child_cat,
                    selling_price=Decimal("1200.00"),
                )

    def test_category_parent_child_relationships_and_cycle_prevention(self):
        """Verifies self-referencing parent-child relationships and cycle validation."""
        grandchild = Category.objects.create(
            name="Embroidered Linen Abayas",
            parent=self.child_cat,
        )
        descendant_ids = self.parent_cat.get_descendant_ids(include_self=True)
        self.assertEqual(
            set(descendant_ids),
            {self.parent_cat.id, self.child_cat.id, grandchild.id},
        )

        # Self-parenting must raise ValidationError
        self.parent_cat.parent = self.parent_cat
        with self.assertRaises(ValidationError):
            self.parent_cat.clean()

        # Deep cycle (parent -> grandchild -> child -> parent) must raise ValidationError
        self.parent_cat.parent = grandchild
        with self.assertRaises(ValidationError):
            self.parent_cat.clean()

    def test_product_price_and_compare_at_price_validation(self):
        """Verifies DecimalField selling_price, optional compare_at_price, discount math, and constraints."""
        discounted = Product.objects.create(
            name="Discounted Khimar",
            sku="DK-200",
            category=self.child_cat,
            selling_price=Decimal("950.00"),
            compare_at_price=Decimal("1200.00"),
        )
        self.assertTrue(discounted.has_valid_discount)
        self.assertEqual(discounted.current_price, Decimal("950.00"))
        self.assertEqual(discounted.original_price, Decimal("1200.00"))
        self.assertEqual(discounted.discount_savings, Decimal("250.00"))
        self.assertEqual(discounted.discount_percentage, 21)

        # Non-discounted product
        regular = Product.objects.create(
            name="Regular Khimar",
            sku="RK-201",
            category=self.child_cat,
            selling_price=Decimal("1100.00"),
        )
        self.assertFalse(regular.has_valid_discount)
        self.assertEqual(regular.current_price, Decimal("1100.00"))
        self.assertIsNone(regular.original_price)
        self.assertEqual(regular.discount_savings, Decimal("0.00"))

        # Compare-at price less than selling price is rejected by validation
        invalid_compare = Product(
            name="Invalid Compare Price",
            sku="ICP-202",
            category=self.child_cat,
            selling_price=Decimal("1000.00"),
            compare_at_price=Decimal("800.00"),
        )
        with self.assertRaises(ValidationError):
            invalid_compare.full_clean()

        # Negative selling price is rejected by validation
        negative_price = Product(
            name="Negative Price",
            sku="NP-203",
            category=self.child_cat,
            selling_price=Decimal("-50.00"),
        )
        with self.assertRaises(ValidationError):
            negative_price.full_clean()

    def test_product_image_ordering_and_single_primary_flag(self):
        """Verifies ProductImage display_order and primary-image flag enforcement."""
        product = Product.objects.create(
            name="Gallery Product",
            sku="GP-300",
            category=self.child_cat,
            selling_price=Decimal("800.00"),
        )
        img1 = ProductImage.objects.create(
            product=product,
            image_url="/static/img/fixtures/lf104-primary.svg",
            alt_text="First image",
            display_order=2,
            is_primary=True,
        )
        img2 = ProductImage.objects.create(
            product=product,
            image_url="/static/img/fixtures/lf104-secondary.svg",
            alt_text="Second image promoted to primary",
            display_order=1,
            is_primary=True,
        )
        img1.refresh_from_db()
        img2.refresh_from_db()
        # Setting img2 as primary automatically unsets img1.is_primary
        self.assertFalse(img1.is_primary)
        self.assertTrue(img2.is_primary)
        self.assertEqual(product.primary_image, img2)
        self.assertEqual(product.secondary_image, img1)

    def test_product_variant_options_and_price_overrides(self):
        """Verifies ProductVariant size/color/custom options and price override fallback."""
        product = Product.objects.create(
            name="Multi-Variant Panjabi",
            sku="MVP-400",
            category=self.child_cat,
            selling_price=Decimal("1400.00"),
            has_variants=True,
        )
        std_variant = ProductVariant.objects.create(
            product=product,
            sku="MVP-400-M-BLK",
            size="M",
            color="Black",
            stock_qty=10,
        )
        xl_variant = ProductVariant.objects.create(
            product=product,
            sku="MVP-400-XXL-GOLD",
            size="XXL",
            color="Gold",
            option_name="Embroidery",
            option_value="Heavy Zari",
            price_override=Decimal("1650.00"),
            compare_at_price=Decimal("1900.00"),
            stock_qty=4,
        )

        # Standard variant inherits product selling_price (1400.00)
        self.assertEqual(std_variant.effective_price, Decimal("1400.00"))
        self.assertEqual(std_variant.retail_price, Decimal("1400.00"))
        # XXL variant uses price_override (1650.00)
        self.assertEqual(xl_variant.effective_price, Decimal("1650.00"))
        self.assertIn("Embroidery: Heavy Zari", xl_variant.display_label)

    def test_product_source_tracking_and_uniqueness(self):
        """Verifies ProductSource external identifiers, source URL, sync timestamps, and status transitions."""
        product = Product.objects.create(
            name="Synced Item",
            sku="SI-500",
            category=self.child_cat,
            selling_price=Decimal("990.00"),
        )
        source = ProductSource.objects.create(
            product=product,
            source_name="droploo",
            external_id="DRP-9901",
            external_sku="SI-500",
            source_url="https://backend.droploo.com/api/product/details/synced-item",
        )
        self.assertEqual(source.sync_status, ProductSource.SyncStatus.PENDING)
        self.assertIsNone(source.last_synced_at)

        source.mark_synced(payload_hash="abc123hash")
        source.refresh_from_db()
        self.assertEqual(source.sync_status, ProductSource.SyncStatus.SYNCED)
        self.assertIsNotNone(source.last_synced_at)
        self.assertEqual(source.raw_payload_hash, "abc123hash")

        source.mark_failed("Connection timeout")
        source.refresh_from_db()
        self.assertEqual(source.sync_status, ProductSource.SyncStatus.FAILED)
        self.assertEqual(source.sync_error_message, "Connection timeout")

        # Duplicate (source_name, external_id) is rejected
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductSource.objects.create(
                    product=product,
                    source_name="droploo",
                    external_id="DRP-9901",
                )


class InventoryReservationAndDecrementTests(TestCase):
    """Tests safe inventory validation, reservation, release, and decrement rules."""

    def setUp(self):
        self.cat = Category.objects.create(name="Inventory Test Category")
        self.product = Product.objects.create(
            name="Tracked Hijab",
            sku="TH-100",
            category=self.cat,
            selling_price=Decimal("750.00"),
            track_inventory=True,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="TH-100-STD",
            size="Free Size",
            stock_qty=10,
            reserved_qty=0,
        )

    def test_reserve_release_and_decrement_stock_lifecycle(self):
        """Verifies reserving stock reduces available_stock, releasing restores it, and committing decrements on-hand stock."""
        self.product.refresh_from_db()
        self.assertEqual(self.variant.available_stock, 10)
        self.assertEqual(self.product.available_stock, 10)

        # 1. Reserve 4 units
        InventoryService.reserve_variant_stock(self.variant.id, 4)
        self.variant.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 10)
        self.assertEqual(self.variant.reserved_qty, 4)
        self.assertEqual(self.variant.available_stock, 6)
        self.assertEqual(self.product.available_stock, 6)

        # 2. Attempting to reserve 7 more units fails (only 6 available)
        with self.assertRaises(ValidationError):
            InventoryService.reserve_variant_stock(self.variant.id, 7)

        # 3. Release 1 reserved unit -> 3 reserved, 7 available
        InventoryService.release_variant_stock(self.variant.id, 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.reserved_qty, 3)
        self.assertEqual(self.variant.available_stock, 7)

        # 4. Commit reservation of 3 units (from_reserved=True) -> stock_qty=7, reserved_qty=0, available=7
        InventoryService.decrement_variant_stock(self.variant.id, 3, from_reserved=True)
        self.variant.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 7)
        self.assertEqual(self.variant.reserved_qty, 0)
        self.assertEqual(self.variant.available_stock, 7)
        self.assertEqual(self.product.stock_qty, 7)

        # 5. Direct decrement of 7 units -> stock becomes 0 (out_of_stock)
        InventoryService.decrement_variant_stock(self.variant.id, 7, from_reserved=False)
        self.variant.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.variant.available_stock, 0)
        self.assertFalse(self.product.is_in_stock)
        self.assertEqual(self.product.availability_state, "out_of_stock")

    def test_invalid_quantities_and_inactive_products_are_rejected(self):
        """Verifies zero/negative quantities and inactive products cannot be reserved or decremented."""
        for bad_qty in (0, -3, False):
            with self.assertRaises(ValidationError):
                InventoryService.reserve_variant_stock(self.variant.id, bad_qty)
            with self.assertRaises(ValidationError):
                InventoryService.decrement_variant_stock(self.variant.id, bad_qty)

        # Cannot release more than reserved
        with self.assertRaises(ValidationError):
            InventoryService.release_variant_stock(self.variant.id, 1)

        # Deactivating product prevents stock reservation and purchase decrement
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])
        self.assertFalse(self.product.is_in_stock)
        with self.assertRaises(ValidationError):
            InventoryService.reserve_variant_stock(self.variant.id, 1)
        with self.assertRaises(ValidationError):
            InventoryService.decrement_variant_stock(self.variant.id, 1)


class CatalogViewsQueriesAndSecurityTests(TestCase):
    """Tests product listing, detail, category pages, pagination, search, filters, sorting, and untrusted input handling."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_dev_fixtures")

    def setUp(self):
        self.client = Client()

    def test_product_list_pagination_and_query_preservation(self):
        """Verifies pagination splits catalog items and preserves active filter parameters."""
        with override_settings(CATALOG_PAGINATE_BY=5):
            res_p1 = self.client.get(reverse("catalog:product_list"), {"sort": "price_asc"})
            self.assertEqual(res_p1.status_code, 200)
            self.assertTrue(res_p1.context["is_paginated"])
            self.assertEqual(len(res_p1.context["products"]), 5)
            self.assertEqual(res_p1.context["page_obj"].paginator.count, 12)
            self.assertContains(res_p1, "sort=price_asc&amp;page=2")

            res_p3 = self.client.get(reverse("catalog:product_list"), {"sort": "price_asc", "page": 3})
            self.assertEqual(res_p3.status_code, 200)
            self.assertEqual(len(res_p3.context["products"]), 2)

    def test_price_filters_and_sorting_use_active_selling_price(self):
        """Verifies min_price, max_price, and price_asc/price_desc sorting operate on actual selling_price in BDT."""
        res = self.client.get(
            reverse("catalog:product_list"),
            {"min_price": "600", "max_price": "1000", "sort": "price_asc"},
        )
        self.assertEqual(res.status_code, 200)
        products = list(res.context["products"])
        self.assertGreater(len(products), 0)
        prices = [p.current_price for p in products]
        self.assertEqual(prices, sorted(prices))
        for price in prices:
            self.assertGreaterEqual(price, Decimal("600.00"))
            self.assertLessEqual(price, Decimal("1000.00"))

        # Descending sort check
        res_desc = self.client.get(reverse("catalog:product_list"), {"sort": "price_desc"})
        desc_prices = [p.current_price for p in res_desc.context["products"]]
        self.assertEqual(desc_prices, sorted(desc_prices, reverse=True))

    def test_availability_and_on_sale_filters(self):
        """Verifies in_stock, out_of_stock, and on_sale filters on catalog views."""
        in_stock_res = self.client.get(reverse("catalog:product_list"), {"in_stock": "1"})
        self.assertEqual(in_stock_res.status_code, 200)
        for p in in_stock_res.context["products"]:
            self.assertTrue(p.is_in_stock)
        # Out-of-stock W10 must not be in in_stock results
        skus_in_stock = {p.sku for p in in_stock_res.context["products"]}
        self.assertNotIn("W10", skus_in_stock)

        oos_res = self.client.get(reverse("catalog:product_list"), {"availability": "out_of_stock"})
        self.assertEqual(oos_res.status_code, 200)
        oos_skus = {p.sku for p in oos_res.context["products"]}
        self.assertEqual(oos_skus, {"W10"})

        sale_res = self.client.get(reverse("catalog:product_list"), {"on_sale": "1"})
        self.assertEqual(sale_res.status_code, 200)
        for p in sale_res.context["products"]:
            self.assertTrue(p.has_valid_discount)

    def test_category_page_includes_descendants_and_handles_inactive_category(self):
        """Verifies parent category page lists subcategory products and 404s when category is inactive."""
        parent_cat = Category.objects.get(slug="womens-modest-wear")
        res = self.client.get(parent_cat.get_absolute_url())
        self.assertEqual(res.status_code, 200)
        skus = {p.sku for p in res.context["products"]}
        # Includes products from Woman Khimar (LF104), Salat Hijab (SH103), Woman Jilbab (WJB204)
        self.assertTrue({"LF104", "SH103", "WJB204"}.issubset(skus))

        # Deactivating category returns 404 on CategoryDetailView and hides its products from listing
        sub_cat = Category.objects.get(slug="woman-khimar")
        sub_cat.is_active = False
        sub_cat.save(update_fields=["is_active"])

        res_404 = self.client.get(sub_cat.get_absolute_url())
        self.assertEqual(res_404.status_code, 404)

        shop_res = self.client.get(reverse("catalog:product_list"))
        shop_skus = {p.sku for p in shop_res.context["products"]}
        self.assertNotIn("LF104", shop_skus)

    def test_inactive_product_returns_404_on_detail_and_cannot_be_added_to_cart(self):
        """Verifies inactive products are excluded from detail pages, search, and cart operations."""
        product = Product.objects.get(sku="LF104")
        variant = product.default_variant
        product.is_active = False
        product.save(update_fields=["is_active"])

        detail_res = self.client.get(product.get_absolute_url())
        self.assertEqual(detail_res.status_code, 404)

        search_res = self.client.get(reverse("catalog:search"), {"q": "LF104"})
        self.assertNotContains(search_res, "Lilen Fabric Khimar Set LF104")

        cart_res = self.client.post(
            reverse("cart:add"),
            {"variant_id": variant.id, "quantity": 1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(cart_res.status_code, 404)

    def test_untrusted_query_string_and_client_submitted_prices_are_ignored(self):
        """Verifies malformed query strings do not crash views and client-submitted prices are never trusted."""
        res = self.client.get(
            reverse("catalog:product_list"),
            {
                "min_price": "-9999; DROP TABLE catalog_product;",
                "max_price": "not-a-decimal",
                "sort": "malicious_column; --",
                "availability": "invalid_state",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["current_sort"], "featured")
        self.assertIsNone(res.context["min_price"])
        self.assertIsNone(res.context["max_price"])

        # Attempting to submit a forged price when adding to cart or checking out is ignored
        product = Product.objects.get(sku="LF104")
        variant = product.default_variant
        initial_stock = variant.stock_qty

        add_res = self.client.post(
            reverse("cart:add"),
            {
                "variant_id": variant.id,
                "quantity": 2,
                "price": "1.00",
                "unit_price": "0.01",
                "retail_price": "0.01",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(add_res.status_code, 200)
        # True price is 1350 * 2 = 2,700 BDT, never the forged 1.00
        self.assertEqual(add_res.json()["subtotal"], "৳2,700")

        zone = ShippingZone.objects.filter(code="INSIDE_DHAKA").first()
        checkout_res = self.client.post(
            reverse("orders:checkout"),
            {
                "customer_name": "Rahim Uddin",
                "customer_phone": "+8801711223344",
                "shipping_address": "House 10, Road 5, Dhanmondi",
                "shipping_district": "Dhaka",
                "shipping_zone_id": zone.id,
                "subtotal_amount": "1.00",
                "grand_total": "1.00",
            },
        )
        self.assertEqual(checkout_res.status_code, 302)
        variant.refresh_from_db()
        self.assertEqual(variant.stock_qty, initial_stock - 2)

    def test_django_admin_registrations_and_safe_inventory_forms(self):
        """Verifies catalog models are registered in Django Admin and admin forms enforce safe inventory rules."""
        for model_cls in (Category, Brand, Product, ProductVariant, ProductSource, ProductReview):
            self.assertIn(model_cls, admin_site._registry)

        product = Product.objects.get(sku="LF104")
        bad_variant_form = ProductVariantAdminForm(
            data={
                "product": product.id,
                "sku": "LF104-BAD-STOCK",
                "size_label": "Free Size",
                "color_label": "Green",
                "retail_price": "1350.00",
                "stock_qty": 3,
                "reserved_qty": 8,  # Invalid: reserved_qty > stock_qty
                "low_stock_threshold": 5,
                "is_active": True,
            }
        )
        self.assertFalse(bad_variant_form.is_valid())
        self.assertIn("reserved_qty", bad_variant_form.errors)
