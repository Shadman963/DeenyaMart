"""
Django management command `import_products` for safe, idempotent CSV/JSON product catalog imports.

Usage:
    python manage.py import_products --file products.csv
    python manage.py import_products --file products.json --dry-run
    python manage.py import_products --file products.csv --markup-percent 35
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.importers.models import ImportJobLog
from apps.importers.services import ImportOptions, ProductImportPipeline


class Command(BaseCommand):
    help = (
        "Imports or updates products from an authorized CSV or JSON catalog export with "
        "currency validation, supplier cost vs. retail selling price separation, duplicate detection, "
        "dry-run mode, and row-level error reporting."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            "-f",
            dest="file_path",
            type=str,
            default="",
            help="Path to the CSV or JSON product catalog file to import.",
        )
        parser.add_argument(
            "--format",
            dest="input_format",
            choices=["auto", "csv", "json"],
            default="auto",
            help="Input file format (auto-detected from .csv or .json extension by default).",
        )
        parser.add_argument(
            "--source",
            dest="source_name",
            type=str,
            default=getattr(settings, "IMPORT_DEFAULT_SOURCE_NAME", "droploo"),
            help="External supplier/source identifier (default: droploo).",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            default=False,
            help="Validate and simulate the import without persisting any catalog changes.",
        )
        parser.add_argument(
            "--overwrite-blank",
            dest="overwrite_blank",
            action="store_true",
            default=False,
            help=(
                "Allow blank/absent optional fields in the source file to overwrite and clear "
                "existing valid data on updated products (default: False)."
            ),
        )
        parser.add_argument(
            "--download-images",
            dest="download_images",
            action="store_true",
            default=False,
            help=(
                "Download external product images when authorized by IMPORT_ALLOW_IMAGE_DOWNLOAD=True "
                "with host allowlist, size limit, Content-Type, and magic-byte verification."
            ),
        )
        parser.add_argument(
            "--markup-percent",
            dest="markup_percent",
            type=str,
            default=None,
            help=(
                "Optional default markup percentage over supplier_cost to compute customer "
                "selling_price when selling_price is omitted in the input row."
            ),
        )
        parser.add_argument(
            "--margin-percent",
            dest="margin_percent",
            type=str,
            default=None,
            help=(
                "Optional default gross margin percentage (0..99.99) to compute customer "
                "selling_price from supplier_cost when selling_price is omitted."
            ),
        )
        parser.add_argument(
            "--allow-negative-margin",
            dest="allow_negative_margin",
            action="store_true",
            default=False,
            help="Explicitly allow customer selling_price below supplier_cost (default: False).",
        )
        parser.add_argument(
            "--strict-categories",
            dest="strict_categories",
            action="store_true",
            default=False,
            help="Reject rows whose category does not exist or have a SupplierCategoryMap entry.",
        )
        parser.add_argument(
            "--default-category",
            dest="default_category_slug",
            type=str,
            default=None,
            help="Optional fallback category slug when a new product row omits category.",
        )
        parser.add_argument(
            "--sync",
            dest="sync_mode",
            action="store_true",
            default=False,
            help=(
                "Live supplier API sync mode (disabled unless a verified, permitted supplier API "
                "integration is available and configured via environment variables)."
            ),
        )

    def handle(self, *args, **options):
        if options.get("sync_mode"):
            raise CommandError(
                "Live supplier API sync (--sync) is not available: https://droploo.com/ is a client-rendered "
                "SPA and does not provide a publicly documented, verifiable, or self-serve authorized B2B "
                "catalog/stock sync API. Real-time stock synchronization is not promised or enabled without "
                "a verified supplier integration. Please export an authorized CSV or JSON catalog file from "
                "your supplier dashboard and run: python manage.py import_products --file <path/to/export.csv>"
            )

        file_path_raw = (options.get("file_path") or "").strip()
        if not file_path_raw:
            raise CommandError(
                "Please specify an import file using `--file <path/to/products.csv|.json>`."
            )

        path = Path(file_path_raw)
        if not path.exists() or not path.is_file():
            raise CommandError(f"Import file does not exist: {path}")

        default_markup = self._parse_cli_decimal(options.get("markup_percent"), "--markup-percent")
        default_margin = self._parse_cli_decimal(options.get("margin_percent"), "--margin-percent")

        if default_markup is not None and default_margin is not None:
            raise CommandError("Specify either --markup-percent or --margin-percent, not both.")
        if default_margin is not None and (default_margin < Decimal("0") or default_margin >= Decimal("100")):
            raise CommandError("--margin-percent must be between 0 and 99.99.")
        if default_markup is not None and default_markup < Decimal("0") and not options.get("allow_negative_margin"):
            raise CommandError("--markup-percent cannot be negative unless --allow-negative-margin is set.")

        import_opts = ImportOptions(
            source_name=(options.get("source_name") or "droploo").strip(),
            dry_run=bool(options.get("dry_run")),
            overwrite_blank=bool(options.get("overwrite_blank")),
            download_images=bool(options.get("download_images")),
            default_markup_percent=default_markup,
            default_margin_percent=default_margin,
            allow_negative_margin=bool(options.get("allow_negative_margin")),
            strict_categories=bool(options.get("strict_categories")),
            default_category_slug=options.get("default_category_slug"),
        )

        pipeline = ProductImportPipeline(options=import_opts)
        job_log = pipeline.run_file_import(
            file_path=path,
            input_format=options.get("input_format", "auto"),
        )

        self._print_report(job_log)

        if job_log.status == ImportJobLog.Status.FAILED and job_log.total_records == 0 and job_log.error_count > 0:
            first_err = job_log.row_errors[0]["error"] if job_log.row_errors else "Import failed."
            raise CommandError(first_err)

    @staticmethod
    def _parse_cli_decimal(raw_val: str | None, flag_name: str) -> Decimal | None:
        if raw_val is None or str(raw_val).strip() == "":
            return None
        try:
            dec = Decimal(str(raw_val).strip().rstrip("%"))
            if dec.is_nan() or dec.is_infinite():
                raise InvalidOperation
            return dec.quantize(Decimal("0.01"))
        except InvalidOperation as exc:
            raise CommandError(f"Invalid decimal value '{raw_val}' for {flag_name}.") from exc

    def _print_report(self, job_log: ImportJobLog):
        mode_banner = "DRY-RUN (NO DB CHANGES SAVED)" if job_log.is_dry_run else "LIVE IMPORT"
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"=== DeenyaMart Product Import Summary [{mode_banner}] ==="))
        self.stdout.write(f"  Job ID          : {job_log.id}")
        self.stdout.write(f"  Source          : {job_log.source_name} ({job_log.source_type})")
        self.stdout.write(f"  File            : {job_log.source_reference}")
        self.stdout.write(f"  Status          : {job_log.status}")
        self.stdout.write(f"  Started At      : {job_log.started_at.isoformat()}")
        if job_log.finished_at:
            self.stdout.write(f"  Finished At     : {job_log.finished_at.isoformat()}")
        self.stdout.write(f"  Total Rows      : {job_log.total_records}")
        self.stdout.write(f"  Created         : {job_log.created_count}")
        self.stdout.write(f"  Updated         : {job_log.updated_count} (Unchanged: {job_log.unchanged_count})")
        self.stdout.write(f"  Duplicates      : {job_log.duplicate_count}")
        self.stdout.write(f"  Warnings        : {job_log.warning_count}")
        self.stdout.write(f"  Row Errors      : {job_log.error_count}")

        if job_log.row_warnings:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("--- Row Warnings ---"))
            for w in job_log.row_warnings:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Row {w.get('row')} [external_id={w.get('external_id') or '-'}, "
                        f"sku={w.get('sku') or '-'}] ({w.get('field')}): {w.get('warning')}"
                    )
                )

        if job_log.row_errors:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("--- Row Errors ---"))
            for err in job_log.row_errors:
                self.stdout.write(
                    self.style.ERROR(
                        f"  Row {err.get('row')} [external_id={err.get('external_id') or '-'}, "
                        f"sku={err.get('sku') or '-'}] ({err.get('field')}): {err.get('error')}"
                    )
                )
        self.stdout.write("")
