"""
Automated tests for the DeenyaMart product import pipeline (`apps.importers`).
Uses locally created fake CSV and JSON fixtures in temporary directories—never
fabricated claims about live Droploo inventory.
"""
import io
import json
import shutil
import tempfile
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.catalog.models import (
    Brand,
    Category,
    Product,
    ProductImage,
    ProductSource,
    ProductVariant,
)
from apps.importers.models import ImportJobLog, SupplierCategoryMap, SupplierProductMap


# Minimal valid 1x1 PNG binary payload (for testing magic-byte & Content-Type checks)
VALID_1X1_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeHTTPResponse:
    """Mock context-manager response for `urllib.request.urlopen`."""

    def __init__(self, body: bytes, content_type: str = "image/png", status: int = 200, content_length: int | None = None):
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, amt: int = -1) -> bytes:
        return self._stream.read(amt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class ProductImportPipelineTests(TestCase):
    """Tests CSV/JSON imports, pricing/margin separation, currency validation, idempotency, dry-run, and image security."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="deenyamart_import_test_"))
        self.media_dir = self.temp_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_csv(self, filename: str, headers: list[str], rows: list[list[str]]) -> Path:
        import csv

        file_path = self.temp_dir / filename
        with file_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return file_path

    def _write_json(self, filename: str, data) -> Path:
        file_path = self.temp_dir / filename
        file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return file_path

    def test_csv_import_creates_products_and_only_populates_supplied_fields(self):
        """
        Verifies importing a fake CSV creates products, categories, brands, ProductSource,
        SupplierProductMap, and ImportJobLog, and ONLY creates variants, images, or stock
        where the source row actually supplies them.
        """
        csv_path = self._write_csv(
            "fake_products.csv",
            headers=[
                "external_id",
                "sku",
                "name",
                "category",
                "subcategory",
                "brand",
                "currency",
                "supplier_cost",
                "selling_price",
                "compare_at_price",
                "stock",
                "short_description",
                "description",
                "images",
                "variants",
            ],
            rows=[
                # Row 2: Full product with variants, stock, and existing static fixture images
                [
                    "FAKE-101",
                    "FK-KHIMAR-101",
                    "Fake Test Linen Khimar 101",
                    "Women's Modest Wear",
                    "Woman Khimar",
                    "Natural Linen",
                    "BDT",
                    "500.00",
                    "800.00",
                    "1000.00",
                    "12",
                    "Short summary for fake khimar 101.",
                    "<p>Detailed description with <script>alert('xss')</script><strong>safe HTML</strong>.</p>",
                    "/static/img/fixtures/lf104-primary.svg|/static/img/fixtures/lf104-secondary.svg",
                    "FK-KHIMAR-101-M:M:Emerald:7:800.00:500.00;FK-KHIMAR-101-L:L:Emerald:5:850.00:520.00",
                ],
                # Row 3: Minimal product WITHOUT variants, WITHOUT images, and WITHOUT stock
                [
                    "FAKE-102",
                    "FK-SIMPLE-102",
                    "Fake Minimal Tasbih Counter 102",
                    "Smart Gadgets",
                    "",
                    "",
                    "৳",
                    "120.00",
                    "৳220.00",
                    "",
                    "",
                    "",
                    "Simple plain text description.",
                    "",
                    "",
                ],
            ],
        )

        out = io.StringIO()
        call_command("import_products", file_path=str(csv_path), stdout=out)

        job_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(job_log.status, ImportJobLog.Status.COMPLETED)
        self.assertEqual(job_log.total_records, 2)
        self.assertEqual(job_log.created_count, 2)
        self.assertEqual(job_log.updated_count, 0)
        self.assertEqual(job_log.error_count, 0)
        self.assertIsNotNone(job_log.started_at)
        self.assertIsNotNone(job_log.finished_at)

        # Verify Product 1 (with variants, images, stock, and sanitized HTML)
        p1 = Product.objects.get(sku="FK-KHIMAR-101")
        self.assertEqual(p1.currency, "BDT")
        self.assertEqual(p1.supplier_cost, Decimal("500.00"))
        self.assertEqual(p1.selling_price, Decimal("800.00"))
        self.assertEqual(p1.compare_at_price, Decimal("1000.00"))
        self.assertEqual(p1.margin_amount, Decimal("300.00"))
        self.assertEqual(p1.margin_percentage, Decimal("37.50"))
        self.assertEqual(p1.markup_percentage, Decimal("60.00"))
        self.assertTrue(p1.has_variants)
        self.assertTrue(p1.track_inventory)
        self.assertEqual(p1.available_stock, 12)
        self.assertEqual(p1.variants.count(), 2)
        self.assertEqual(p1.images.count(), 2)
        self.assertNotIn("<script>", p1.description_html)
        self.assertIn("<strong>safe HTML</strong>", p1.description_html)

        v_large = ProductVariant.objects.get(sku="FK-KHIMAR-101-L")
        self.assertEqual(v_large.supplier_cost, Decimal("520.00"))
        self.assertEqual(v_large.effective_price, Decimal("850.00"))
        self.assertEqual(v_large.margin_amount, Decimal("330.00"))

        # Verify Product 2 (no variants, no images, no stock supplied -> none fabricated!)
        p2 = Product.objects.get(sku="FK-SIMPLE-102")
        self.assertFalse(p2.has_variants)
        self.assertEqual(p2.variants.count(), 0)
        self.assertEqual(p2.images.count(), 0)
        self.assertFalse(p2.track_inventory)
        self.assertEqual(p2.stock_qty, 0)
        self.assertIsNone(p2.compare_at_price)
        self.assertEqual(p2.supplier_cost, Decimal("120.00"))
        self.assertEqual(p2.selling_price, Decimal("220.00"))

        # Verify ProductSource and SupplierProductMap records
        src1 = ProductSource.objects.get(source_name="droploo", external_id="FAKE-101")
        self.assertEqual(src1.product, p1)
        self.assertEqual(src1.sync_status, ProductSource.SyncStatus.SYNCED)
        self.assertIsNotNone(src1.last_synced_at)

        smap1 = SupplierProductMap.objects.get(source_name="droploo", external_product_id="FAKE-101")
        self.assertEqual(smap1.supplier_cost, Decimal("500.00"))
        self.assertEqual(smap1.applied_margin_percent, Decimal("37.50"))

    def test_supplier_cost_is_never_assumed_as_retail_selling_price(self):
        """
        Verifies that rows supplying only `supplier_cost` (without `selling_price` or an
        explicit margin/markup rule) are rejected, and that `--markup-percent` or
        `--margin-percent` computes `selling_price` cleanly when requested.
        """
        csv_path = self._write_csv(
            "supplier_cost_only.csv",
            headers=["external_id", "sku", "name", "category", "supplier_cost", "selling_price"],
            rows=[
                # Missing selling_price -> must NOT assume supplier_cost (400.00) is retail price!
                ["FAKE-201", "FK-COSTONLY-201", "Cost Only Item", "Home & Kitchen", "400.00", ""],
                # Selling price below supplier_cost -> negative margin rejected by default!
                ["FAKE-202", "FK-NEG-202", "Below Cost Item", "Home & Kitchen", "500.00", "450.00"],
            ],
        )

        call_command("import_products", file_path=str(csv_path), stdout=io.StringIO())
        job_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(job_log.created_count, 0)
        self.assertEqual(job_log.error_count, 2)
        self.assertEqual(Product.objects.count(), 0)
        self.assertIn("supplier_cost is never assumed", job_log.row_errors[0]["error"])
        self.assertIn("cannot be less than supplier_cost", job_log.row_errors[1]["error"])

        # Now test importing with explicit --markup-percent 50 on a supplier_cost-only file
        csv_markup = self._write_csv(
            "supplier_cost_with_markup.csv",
            headers=["external_id", "sku", "name", "category", "supplier_cost"],
            rows=[
                ["FAKE-203", "FK-MARKUP-203", "Markup Derived Item", "Home & Kitchen", "400.00"],
            ],
        )
        call_command(
            "import_products",
            file_path=str(csv_markup),
            markup_percent="50",
            stdout=io.StringIO(),
        )
        p_markup = Product.objects.get(sku="FK-MARKUP-203")
        self.assertEqual(p_markup.supplier_cost, Decimal("400.00"))
        self.assertEqual(p_markup.selling_price, Decimal("600.00"))
        self.assertEqual(p_markup.markup_percentage, Decimal("50.00"))
        self.assertEqual(p_markup.margin_amount, Decimal("200.00"))

        # Test row-level margin_percent (20% margin on 800 cost -> 800 / 0.80 = 1000.00 selling_price)
        csv_margin = self._write_csv(
            "supplier_cost_with_margin.csv",
            headers=["external_id", "sku", "name", "category", "supplier_cost", "margin_percent"],
            rows=[
                ["FAKE-204", "FK-MARGIN-204", "Margin Derived Item", "Home & Kitchen", "800.00", "20%"],
            ],
        )
        call_command("import_products", file_path=str(csv_margin), stdout=io.StringIO())
        p_margin = Product.objects.get(sku="FK-MARGIN-204")
        self.assertEqual(p_margin.supplier_cost, Decimal("800.00"))
        self.assertEqual(p_margin.selling_price, Decimal("1000.00"))
        self.assertEqual(p_margin.margin_percentage, Decimal("20.00"))

    def test_explicit_currency_parsing_and_validation(self):
        """
        Verifies valid BDT formats ('BDT', '৳', 'Tk') pass while foreign currencies ('USD', '$', 'EUR'),
        conflicting currencies, and invalid currency codes are rejected at the row level.
        """
        csv_path = self._write_csv(
            "currency_test.csv",
            headers=["external_id", "sku", "name", "category", "currency", "selling_price"],
            rows=[
                ["CURR-1", "CURR-OK-1", "Valid Taka Symbol", "Smart Gadgets", "BDT", "৳ 1,250.00"],
                ["CURR-2", "CURR-OK-2", "Valid Tk Alias", "Smart Gadgets", "Tk", "850"],
                ["CURR-3", "CURR-BAD-USD", "Foreign USD Row", "Smart Gadgets", "USD", "25.00"],
                ["CURR-4", "CURR-BAD-DOLLAR", "Embedded Dollar Price", "Smart Gadgets", "BDT", "$45.00"],
                ["CURR-5", "CURR-BAD-ISO", "Unknown Currency Code", "Smart Gadgets", "XYZ", "500.00"],
            ],
        )

        call_command("import_products", file_path=str(csv_path), stdout=io.StringIO())
        job_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(job_log.created_count, 2)
        self.assertEqual(job_log.error_count, 3)

        p1 = Product.objects.get(sku="CURR-OK-1")
        self.assertEqual(p1.selling_price, Decimal("1250.00"))
        self.assertEqual(p1.currency, "BDT")

        p2 = Product.objects.get(sku="CURR-OK-2")
        self.assertEqual(p2.selling_price, Decimal("850.00"))
        self.assertEqual(p2.currency, "BDT")

        self.assertFalse(Product.objects.filter(sku__startswith="CURR-BAD").exists())

    def test_idempotent_upserts_and_non_destructive_blank_handling(self):
        """
        Verifies that:
        1. Rerunning the same import file updates existing records instead of duplicating products.
        2. Absent/blank optional fields in an update file do NOT overwrite existing valid data
           unless `--overwrite-blank` is explicitly passed.
        """
        initial_csv = self._write_csv(
            "upsert_initial.csv",
            headers=[
                "external_id",
                "sku",
                "name",
                "category",
                "brand",
                "supplier_cost",
                "selling_price",
                "compare_at_price",
                "stock",
                "description",
                "images",
                "variants",
            ],
            rows=[
                [
                    "FAKE-301",
                    "FK-UPSERT-301",
                    "Initial Panjabi Name",
                    "Men's Modest Wear",
                    "Pure Cotton",
                    "700.00",
                    "1200.00",
                    "1400.00",
                    "10",
                    "Original rich cotton description.",
                    "/static/img/fixtures/vcp108-primary.svg",
                    "FK-UPSERT-301-M:M:Slate:10:1200.00:700.00",
                ]
            ],
        )

        # 1. Initial import
        call_command("import_products", file_path=str(initial_csv), stdout=io.StringIO())
        self.assertEqual(Product.objects.count(), 1)
        prod = Product.objects.get(sku="FK-UPSERT-301")
        initial_pk = prod.pk

        # 2. Rerun exact same file -> idempotent update, no duplicates created
        call_command("import_products", file_path=str(initial_csv), stdout=io.StringIO())
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(ProductVariant.objects.count(), 1)
        self.assertEqual(ProductImage.objects.count(), 1)
        self.assertEqual(ProductSource.objects.count(), 1)
        rerun_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(rerun_log.created_count, 0)
        self.assertEqual(rerun_log.updated_count, 1)

        # 3. Partial update with blank description, blank compare_at_price, blank supplier_cost,
        #    blank brand, blank stock, blank images, and blank variants -> must preserve existing data!
        partial_update_csv = self._write_csv(
            "upsert_partial.csv",
            headers=[
                "external_id",
                "sku",
                "name",
                "category",
                "brand",
                "supplier_cost",
                "selling_price",
                "compare_at_price",
                "stock",
                "description",
                "images",
                "variants",
            ],
            rows=[
                [
                    "FAKE-301",
                    "FK-UPSERT-301",
                    "Updated Panjabi Name",
                    "",
                    "",
                    "",
                    "1150.00",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            ],
        )
        call_command("import_products", file_path=str(partial_update_csv), stdout=io.StringIO())
        prod.refresh_from_db()
        self.assertEqual(prod.pk, initial_pk)
        self.assertEqual(prod.name, "Updated Panjabi Name")
        self.assertEqual(prod.selling_price, Decimal("1150.00"))
        # Existing fields must be preserved when source fields are blank!
        self.assertEqual(prod.description, "Original rich cotton description.")
        self.assertEqual(prod.compare_at_price, Decimal("1400.00"))
        self.assertEqual(prod.supplier_cost, Decimal("700.00"))
        self.assertEqual(prod.brand.name, "Pure Cotton")
        self.assertEqual(prod.available_stock, 10)
        self.assertEqual(prod.images.count(), 1)
        self.assertEqual(prod.variants.count(), 1)

        # 4. Now run with explicit --overwrite-blank -> blank optional fields clear existing data
        call_command(
            "import_products",
            file_path=str(partial_update_csv),
            overwrite_blank=True,
            default_category_slug="mens-modest-wear",
            stdout=io.StringIO(),
        )
        prod.refresh_from_db()
        self.assertEqual(prod.description, "")
        self.assertIsNone(prod.compare_at_price)
        self.assertIsNone(prod.supplier_cost)
        self.assertIsNone(prod.brand)
        self.assertEqual(prod.images.count(), 0)
        self.assertEqual(prod.variants.count(), 0)

    def test_dry_run_mode_rolls_back_all_database_changes(self):
        """Verifies `--dry-run` validates records and writes ImportJobLog without persisting any catalog data."""
        csv_path = self._write_csv(
            "dry_run.csv",
            headers=["external_id", "sku", "name", "category", "supplier_cost", "selling_price"],
            rows=[
                ["DRY-1", "DRY-SKU-1", "Dry Run Product 1", "Women's Modest Wear", "300.00", "550.00"],
                ["DRY-2", "DRY-SKU-2", "Dry Run Invalid Price", "Women's Modest Wear", "300.00", "-50.00"],
            ],
        )

        call_command("import_products", file_path=str(csv_path), dry_run=True, stdout=io.StringIO())

        # Zero products or categories should exist in DB after dry run
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Category.objects.count(), 0)
        self.assertEqual(ProductSource.objects.count(), 0)

        # ImportJobLog records the simulated run
        job_log = ImportJobLog.objects.latest("started_at")
        self.assertTrue(job_log.is_dry_run)
        self.assertEqual(job_log.status, ImportJobLog.Status.DRY_RUN)
        self.assertEqual(job_log.total_records, 2)
        self.assertEqual(job_log.created_count, 1)
        self.assertEqual(job_log.error_count, 1)
        self.assertEqual(job_log.row_errors[0]["row"], 3)
        self.assertEqual(job_log.row_errors[0]["field"], "selling_price")

    def test_duplicate_detection_in_file_and_database_sku_conflicts(self):
        """
        Verifies detection of:
        - Duplicate `external_id` within the same file
        - Duplicate `sku` across different `external_id`s in the same file
        - Duplicate `sku` conflicting with an existing product in the database
        """
        existing_cat = Category.objects.create(name="Existing Cat", slug="existing-cat")
        Product.objects.create(
            name="Pre-existing DB Product",
            sku="DB-TAKEN-SKU",
            category=existing_cat,
            selling_price=Decimal("900.00"),
        )

        csv_path = self._write_csv(
            "duplicates.csv",
            headers=["external_id", "sku", "name", "category", "selling_price"],
            rows=[
                ["DUP-EXT-1", "UNIQUE-SKU-1", "First Valid Item", "Existing Cat", "500.00"],
                # Duplicate external_id in file
                ["DUP-EXT-1", "UNIQUE-SKU-2", "Duplicate Ext ID Item", "Existing Cat", "600.00"],
                # Duplicate SKU in file
                ["DUP-EXT-2", "UNIQUE-SKU-1", "Duplicate File SKU Item", "Existing Cat", "700.00"],
                # Duplicate SKU against existing DB product
                ["DUP-EXT-3", "DB-TAKEN-SKU", "DB Conflict SKU Item", "Existing Cat", "800.00"],
            ],
        )

        call_command("import_products", file_path=str(csv_path), stdout=io.StringIO())
        job_log = ImportJobLog.objects.latest("started_at")

        self.assertEqual(job_log.total_records, 4)
        self.assertEqual(job_log.created_count, 1)
        self.assertEqual(job_log.duplicate_count, 3)
        self.assertEqual(job_log.error_count, 3)
        self.assertEqual(Product.objects.count(), 2)  # Pre-existing + 1 newly created

    def test_category_mapping_and_typo_normalization(self):
        """
        Verifies `SupplierCategoryMap` lookup (by ID and name) as well as built-in
        supplier category typo normalization (e.g. "Man's Cothing" -> "Men's Modest Wear").
        """
        mapped_target = Category.objects.create(name="Custom Curated Decor", slug="custom-curated-decor")
        SupplierCategoryMap.objects.create(
            source_name="droploo",
            external_category_id="999",
            external_name="Supplier Legacy Decor",
            mapped_category=mapped_target,
        )

        json_path = self._write_json(
            "category_map_test.json",
            {
                "products": [
                    {
                        "external_id": "CAT-MAP-1",
                        "sku": "CM-1",
                        "name": "Mapped By External Cat ID",
                        "external_category_id": 999,
                        "selling_price": "450.00",
                    },
                    {
                        "external_id": "CAT-MAP-2",
                        "sku": "CM-2",
                        "name": "Normalized Typo Category Item",
                        "category": "Man's Cothing",
                        "selling_price": "1100.00",
                    },
                ]
            },
        )

        call_command("import_products", file_path=str(json_path), stdout=io.StringIO())
        p1 = Product.objects.get(sku="CM-1")
        p2 = Product.objects.get(sku="CM-2")
        self.assertEqual(p1.category, mapped_target)
        self.assertEqual(p2.category.slug, "mens-modest-wear")
        self.assertEqual(p2.category.name, "Men's Modest Wear")

    def test_authorized_image_downloading_security_limits_and_failure_handling(self):
        """
        Verifies image downloading guardrails:
        - Disabled by default unless IMPORT_ALLOW_IMAGE_DOWNLOAD=True
        - Enforces host allowlist and blocks loopback/private IP SSRF
        - Enforces Content-Type and magic-byte signature validation
        - Enforces max file size limit (IMPORT_MAX_IMAGE_BYTES)
        - Sanitizes unsafe filenames
        - Handles 404/unavailable images as non-fatal warnings so the product still imports
        """
        csv_path = self._write_csv(
            "images_test.csv",
            headers=["external_id", "sku", "name", "category", "selling_price", "images"],
            rows=[
                [
                    "IMG-1",
                    "IMG-SKU-1",
                    "Product With Mixed Images",
                    "Women's Modest Wear",
                    "950.00",
                    (
                        "https://backend.droploo.com/galleryImage/../../Evil Name!! 101.png|"
                        "https://backend.droploo.com/galleryImage/missing-404.png|"
                        "https://backend.droploo.com/galleryImage/fake-html.png|"
                        "https://backend.droploo.com/galleryImage/oversized.png|"
                        "http://127.0.0.1/private-ssrf.png"
                    ),
                ]
            ],
        )

        def _mock_urlopen(req, timeout=10):
            url = req.full_url
            if "Evil%20Name" in url:
                return FakeHTTPResponse(VALID_1X1_PNG_BYTES, content_type="image/png", status=200)
            if "missing-404.png" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", hdrs={}, fp=None)
            if "fake-html.png" in url:
                return FakeHTTPResponse(b"<html>not an image</html>", content_type="text/html", status=200)
            if "oversized.png" in url:
                return FakeHTTPResponse(
                    VALID_1X1_PNG_BYTES * 100,
                    content_type="image/png",
                    status=200,
                    content_length=10 * 1024 * 1024,
                )
            raise AssertionError(f"Unexpected network call to {url}")

        with override_settings(
            MEDIA_ROOT=self.media_dir,
            IMPORT_ALLOW_IMAGE_DOWNLOAD=True,
            IMPORT_ALLOWED_IMAGE_HOSTS=["backend.droploo.com"],
            IMPORT_MAX_IMAGE_BYTES=1024,  # 1 KB limit for test
            IMPORT_IMAGE_TIMEOUT_SECONDS=5,
        ):
            with patch("urllib.request.urlopen", side_effect=_mock_urlopen):
                call_command(
                    "import_products",
                    file_path=str(csv_path),
                    download_images=True,
                    stdout=io.StringIO(),
                )

        job_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(job_log.status, ImportJobLog.Status.COMPLETED)
        self.assertEqual(job_log.created_count, 1)
        self.assertEqual(job_log.error_count, 0)
        # 4 bad images (404, text/html, oversized, 127.0.0.1 SSRF blocked) logged as warnings
        self.assertEqual(job_log.warning_count, 4)

        product = Product.objects.get(sku="IMG-SKU-1")
        # Only the 1 valid, authorized PNG image was attached
        self.assertEqual(product.images.count(), 1)
        saved_img = product.images.first()
        self.assertTrue(saved_img.is_primary)
        self.assertTrue(bool(saved_img.image.name))
        # Verify filename was sanitized (no '..', no spaces, no '!')
        basename = Path(saved_img.image.name).name
        self.assertNotIn("..", basename)
        self.assertNotIn(" ", basename)
        self.assertNotIn("!", basename)
        self.assertTrue(basename.endswith(".png"))

    def test_sync_mode_is_guarded_when_no_verified_integration_exists(self):
        """Verifies `--sync` refuses to run and does not promise unverified real-time stock sync."""
        with self.assertRaises(CommandError) as ctx:
            call_command("import_products", sync_mode=True, stdout=io.StringIO())
        self.assertIn("not available", str(ctx.exception))

    def test_row_level_error_reporting_for_missing_fields_and_invalid_values(self):
        """
        Verifies row-level error isolation for missing identifiers, missing name, missing category,
        compare_at_price < selling_price, non-numeric prices, and negative stock while valid rows commit.
        """
        csv_path = self._write_csv(
            "row_errors.csv",
            headers=[
                "external_id",
                "sku",
                "name",
                "category",
                "selling_price",
                "compare_at_price",
                "stock",
            ],
            rows=[
                # Row 2: Missing both external_id and sku
                ["", "", "No Identifier Item", "Smart Gadgets", "500.00", "", "5"],
                # Row 3: Missing name
                ["ERR-2", "ERR-SKU-2", "", "Smart Gadgets", "500.00", "", "5"],
                # Row 4: Missing category
                ["ERR-3", "ERR-SKU-3", "No Category Item", "", "500.00", "", "5"],
                # Row 5: compare_at_price < selling_price
                ["ERR-4", "ERR-SKU-4", "Bad Compare Price", "Smart Gadgets", "800.00", "650.00", "5"],
                # Row 6: Non-numeric selling_price
                ["ERR-5", "ERR-SKU-5", "Non Numeric Price", "Smart Gadgets", "free-of-cost", "", "5"],
                # Row 7: Negative stock
                ["ERR-6", "ERR-SKU-6", "Negative Stock Item", "Smart Gadgets", "500.00", "", "-3"],
                # Row 8: Valid row -> must still succeed!
                ["VALID-7", "VALID-SKU-7", "Valid Survivor Item", "Smart Gadgets", "750.00", "900.00", "4"],
            ],
        )

        call_command("import_products", file_path=str(csv_path), stdout=io.StringIO())
        job_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(job_log.status, ImportJobLog.Status.COMPLETED_WITH_ERRORS)
        self.assertEqual(job_log.total_records, 7)
        self.assertEqual(job_log.created_count, 1)
        self.assertEqual(job_log.error_count, 6)

        error_fields = [e["field"] for e in job_log.row_errors]
        self.assertEqual(
            error_fields,
            ["external_id", "name", "category", "compare_at_price", "selling_price", "stock"],
        )
        self.assertEqual(Product.objects.count(), 1)
        survivor = Product.objects.get(sku="VALID-SKU-7")
        self.assertEqual(survivor.selling_price, Decimal("750.00"))
        self.assertEqual(survivor.compare_at_price, Decimal("900.00"))
        self.assertEqual(survivor.stock_qty, 4)

    def test_image_download_refused_when_env_authorization_is_disabled(self):
        """Verifies `--download-images` does not make network calls when IMPORT_ALLOW_IMAGE_DOWNLOAD=False."""
        csv_path = self._write_csv(
            "unauth_img.csv",
            headers=["external_id", "sku", "name", "category", "selling_price", "images"],
            rows=[
                [
                    "UNAUTH-1",
                    "UNAUTH-SKU-1",
                    "Unauth Image Product",
                    "Smart Gadgets",
                    "600.00",
                    "https://backend.droploo.com/galleryImage/photo.png",
                ]
            ],
        )

        with override_settings(IMPORT_ALLOW_IMAGE_DOWNLOAD=False):
            with patch("urllib.request.urlopen") as mock_urlopen:
                call_command(
                    "import_products",
                    file_path=str(csv_path),
                    download_images=True,
                    stdout=io.StringIO(),
                )
                mock_urlopen.assert_not_called()

        job_log = ImportJobLog.objects.latest("started_at")
        self.assertEqual(job_log.created_count, 1)
        self.assertEqual(job_log.warning_count, 1)
        self.assertIn("IMPORT_ALLOW_IMAGE_DOWNLOAD", job_log.row_warnings[0]["warning"])

