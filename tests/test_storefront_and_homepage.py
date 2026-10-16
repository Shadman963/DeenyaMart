"""Automated tests for DeenyaMart design system, homepage sections, product card rules, and interactive endpoints."""
from decimal import Decimal
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Category, Product
from apps.core.models import NewsletterSubscriber, SiteSettings


class StorefrontHomepageTests(TestCase):
    """Verifies all 11 homepage sections, product card requirements, and interactive endpoints."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_dev_fixtures")

    def setUp(self):
        self.client = Client()

    def test_homepage_renders_all_required_sections_and_featured_fallback(self):
        """When no real sales exist (total_sales == 0), section 8 must be titled 'Featured Products'."""
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)

        # 1. Slim announcement bar
        self.assertContains(response, "Cash on Delivery across Bangladesh")
        # 2 & 3. Header & dynamic categories
        self.assertContains(response, "Deenya")
        self.assertContains(response, "Women")
        self.assertContains(response, "Men")
        self.assertContains(response, "Kids")
        # 4. Hero section
        self.assertContains(response, "Refined Modest Wear")
        self.assertContains(response, "Shop Now")
        # 6. New Arrivals
        self.assertContains(response, "New Arrivals")
        # 7. Promotional Collection Banner
        self.assertContains(response, "The Linen")
        # 8. Featured Products when total_sales == 0
        self.assertFalse(response.context["has_real_sales_data"])
        self.assertEqual(response.context["popular_section_title"], "Featured Products")
        self.assertContains(
            response,
            '<h2 id="featured-or-bestselling-heading" class="dm-section__title">Featured Products</h2>',
        )
        self.assertNotContains(
            response,
            '<h2 id="featured-or-bestselling-heading" class="dm-section__title">Best-Selling Products</h2>',
        )
        # 9. Verified Trust Section
        self.assertContains(response, "Inside Dhaka City")
        self.assertContains(response, "Cash on Delivery (COD)")
        # 10 & 11. Newsletter & Footer
        self.assertContains(response, reverse("core:newsletter_subscribe"))
        self.assertContains(response, "7-Day Exchange")

    def test_section_8_switches_to_best_selling_when_real_sales_exist(self):
        """When real sales data exists (total_sales > 0), section 8 switches to 'Best-Selling Products'."""
        product = Product.objects.first()
        product.total_sales = 15
        product.save(update_fields=["total_sales"])

        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_real_sales_data"])
        self.assertEqual(response.context["popular_section_title"], "Best-Selling Products")
        self.assertContains(
            response,
            '<h2 id="featured-or-bestselling-heading" class="dm-section__title">Best-Selling Products</h2>',
        )

    def test_product_genuine_discount_and_availability_logic(self):
        """Verifies genuine discount calculation and that non-discounted products omit original_price."""
        regular_only = Product.objects.get(sku_prefix="LF104")
        self.assertFalse(regular_only.has_valid_discount)
        self.assertIsNone(regular_only.original_price)
        self.assertEqual(regular_only.discount_percentage, 0)
        self.assertEqual(regular_only.current_price, Decimal("1350.00"))

        discounted = Product.objects.get(sku_prefix="ES71")
        self.assertTrue(discounted.has_valid_discount)
        self.assertEqual(discounted.original_price, Decimal("1250.00"))
        self.assertEqual(discounted.current_price, Decimal("1000.00"))
        self.assertEqual(discounted.discount_percentage, 20)
        self.assertEqual(discounted.discount_savings, Decimal("250.00"))
        self.assertEqual(discounted.availability_state, "low_stock")

        out_of_stock = Product.objects.get(sku_prefix="W10")
        self.assertFalse(out_of_stock.is_in_stock)
        self.assertEqual(out_of_stock.availability_state, "out_of_stock")

    def test_cart_add_and_update_endpoints(self):
        """Verifies adding a variant to cart and updating quantity via JSON AJAX."""
        product = Product.objects.get(sku_prefix="LF104")
        variant = product.default_variant

        res = self.client.post(
            reverse("cart:add"),
            {"variant_id": variant.id, "quantity": 2},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["cart_count"], 2)

    def test_wishlist_toggle_endpoint(self):
        """Verifies toggling a product in the session-backed wishlist."""
        product = Product.objects.get(sku_prefix="SH103")
        res = self.client.post(
            reverse("accounts:wishlist_toggle"),
            {"product_id": product.id},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["added"])
        self.assertEqual(data["wishlist_count"], 1)

    def test_newsletter_server_side_validation_and_persistence(self):
        """Verifies server-side email validation and persistence for newsletter signup."""
        bad_res = self.client.post(
            reverse("core:newsletter_subscribe"),
            {"email": "not-an-email"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(bad_res.status_code, 400)
        self.assertFalse(bad_res.json()["ok"])

        good_res = self.client.post(
            reverse("core:newsletter_subscribe"),
            {"email": "Farhana@Example.com"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(good_res.status_code, 200)
        self.assertTrue(good_res.json()["ok"])
        self.assertTrue(NewsletterSubscriber.objects.filter(email="farhana@example.com").exists())

    def test_search_and_live_suggestions_endpoints(self):
        """Verifies catalog search page and JSON autocomplete suggestions."""
        search_res = self.client.get(reverse("catalog:search"), {"q": "Khimar"})
        self.assertEqual(search_res.status_code, 200)
        self.assertContains(search_res, "Lilen Fabric Khimar Set LF104")

        sugg_res = self.client.get(reverse("catalog:search_suggestions"), {"q": "LF104"})
        self.assertEqual(sugg_res.status_code, 200)
        payload = sugg_res.json()
        self.assertEqual(len(payload["products"]), 1)
        self.assertEqual(payload["products"][0]["sku"], "LF104")
