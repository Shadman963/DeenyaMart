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
                self.stdout.write("Catalog already populated; skipping fixture load.")
            else:
                self.stdout.write(f"Empty catalog detected; loading fixture '{options['fixture']}'...")
                call_command("loaddata", options["fixture"])

        self._ensure_superuser()

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
