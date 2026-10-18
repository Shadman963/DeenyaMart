"""
Post-deploy bootstrap command (used by Render's build step).

- Loads the bundled catalog fixture only when the database has no products yet,
  so admin edits are never overwritten on subsequent deploys.
- Optionally creates/updates a superuser from environment variables, since
  Render's free tier has no interactive shell.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.catalog.models import Product


class Command(BaseCommand):
    help = "Load initial catalog data (if DB is empty) and ensure an admin superuser exists."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            default="initial_catalog",
            help="Fixture name to load when the catalog is empty (default: initial_catalog).",
        )
        parser.add_argument(
            "--skip-data",
            action="store_true",
            help="Skip loading the catalog fixture.",
        )

    def handle(self, *args, **options):
        if not options["skip_data"]:
            if Product.objects.exists():
                self.stdout.write(
                    f"Catalog already populated; syncing categories, product category mappings, and site settings from '{options['fixture']}'..."
                )
                self._sync_catalog_taxonomy(options["fixture"])
            else:
                self.stdout.write(f"Empty catalog detected; loading fixture '{options['fixture']}'...")
                call_command("loaddata", options["fixture"])

        self._ensure_superuser()

    def _sync_catalog_taxonomy(self, fixture_name: str):
        import json
        from pathlib import Path
        from apps.catalog.models import Category
        from apps.core.models import SiteSettings

        fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / f"{fixture_name}.json"
        if not fixture_path.exists():
            return

        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        # 1. Sync SiteSettings
        for entry in data:
            if entry.get("model") == "core.sitesettings":
                fields = entry.get("fields", {})
                SiteSettings.objects.filter(pk=entry["pk"]).update(
                    announcement_cta_text=fields.get("announcement_cta_text", ""),
                    announcement_cta_url=fields.get("announcement_cta_url", ""),
                )

        # 2. Sync Categories (parents first, then children)
        cat_entries = [e for e in data if e.get("model") == "catalog.category"]
        pk_to_cat = {}
        for is_child_pass in (False, True):
            for entry in cat_entries:
                fields = entry["fields"]
                parent_pk = fields.get("parent")
                if bool(parent_pk) != is_child_pass:
                    continue
                parent_obj = pk_to_cat.get(parent_pk) if parent_pk else None
                cat_obj, _ = Category.objects.update_or_create(
                    slug=fields["slug"],
                    defaults={
                        "name": fields["name"],
                        "parent": parent_obj,
                        "subtitle": fields.get("subtitle", ""),
                        "description": fields.get("description", ""),
                        "image_url": fields.get("image_url", ""),
                        "accent_tone": fields.get("accent_tone", "emerald"),
                        "is_featured": fields.get("is_featured", False),
                        "is_active": fields.get("is_active", True),
                        "sort_order": fields.get("sort_order", 0),
                    },
                )
                pk_to_cat[entry["pk"]] = cat_obj

        # 3. Sync Product -> Category assignments by SKU
        for entry in data:
            if entry.get("model") == "catalog.product":
                fields = entry["fields"]
                target_cat = pk_to_cat.get(fields.get("category"))
                if target_cat and fields.get("sku"):
                    Product.objects.filter(sku=fields["sku"]).exclude(category=target_cat).update(
                        category=target_cat
                    )

    def _ensure_superuser(self):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_USERNAME/PASSWORD not set; skipping superuser setup.")
            return

        User = get_user_model()
        user, created = User.objects.get_or_create(username=username, defaults={"email": email})
        user.is_staff = True
        user.is_superuser = True
        if email:
            user.email = email
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' {'created' if created else 'updated'}."))
