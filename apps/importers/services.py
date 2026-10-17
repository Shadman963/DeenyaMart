"""
Reusable, safe product-import pipeline for DeenyaMart.

Supports documented CSV and JSON catalog schemas with:
- Explicit currency parsing and validation against store currency (BDT).
- Strict separation of supplier cost, customer retail selling price, and margin.
- Idempotent upserts keyed by (source_name, external_id) or stable SKU.
- Non-destructive updates (absent/blank source fields never overwrite existing valid data unless requested).
- Duplicate detection (in-file duplicate external_ids/SKUs and DB-level SKU conflicts).
- Dry-run mode with full savepoint rollback and row-level error/warning reporting.
- Authorized-only image downloading with SSRF protection, host allowlisting, timeouts,
  file-size limits, Content-Type + magic-byte checks, safe filenames, and graceful failure handling.
"""
import csv
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import (
    Brand,
    Category,
    Product,
    ProductImage,
    ProductSource,
    ProductVariant,
)
from .models import ImportJobLog, SupplierCategoryMap, SupplierProductMap


class ImportValidationError(Exception):
    """Raised when a single product record/row fails validation."""

    def __init__(self, field_name: str, message: str, is_duplicate: bool = False):
        super().__init__(message)
        self.field_name = field_name
        self.message = message
        self.is_duplicate = is_duplicate


@dataclass
class ImportOptions:
    """Configuration options for an import job run."""

    source_name: str = "droploo"
    dry_run: bool = False
    overwrite_blank: bool = False
    download_images: bool = False
    default_markup_percent: Decimal | None = None
    default_margin_percent: Decimal | None = None
    allow_negative_margin: bool = False
    strict_categories: bool = False
    default_category_slug: str | None = None


# Recognized currency symbols/aliases -> ISO 4217 3-letter code
CURRENCY_TOKEN_MAP = {
    "BDT": "BDT",
    "৳": "BDT",
    "TK": "BDT",
    "TK.": "BDT",
    "TAKA": "BDT",
    "USD": "USD",
    "$": "USD",
    "US$": "USD",
    "EUR": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "£": "GBP",
    "INR": "INR",
    "₹": "INR",
    "RS": "INR",
    "RS.": "INR",
}

VALID_ISO_CURRENCIES = {"BDT", "USD", "EUR", "GBP", "INR", "AUD", "CAD", "SAR", "AED", "MYR", "SGD"}

# Built-in supplier category normalization (handles known Droploo categories & typos)
DEFAULT_SUPPLIER_CATEGORY_ALIASES = {
    # Droploo / Kidsya known category names & typos -> (Parent Name, Parent Slug, Child Name, Child Slug)
    "man's cothing": ("Men's Modest Wear", "mens-modest-wear", None, None),
    "men's fashions": ("Men's Modest Wear", "mens-modest-wear", None, None),
    "mens fashions": ("Men's Modest Wear", "mens-modest-wear", None, None),
    "men's clothing": ("Men's Modest Wear", "mens-modest-wear", None, None),
    "men's panjabi": ("Men's Modest Wear", "mens-modest-wear", "Panjabi", "panjabi"),
    "panjabi": ("Men's Modest Wear", "mens-modest-wear", "Panjabi", "panjabi"),
    "men's jubba": ("Men's Modest Wear", "mens-modest-wear", "Jubba", "jubba"),
    "jubba": ("Men's Modest Wear", "mens-modest-wear", "Jubba", "jubba"),
    "men's kabli": ("Men's Modest Wear", "mens-modest-wear", "Kabli", "kabli"),
    "kabli": ("Men's Modest Wear", "mens-modest-wear", "Kabli", "kabli"),
    "woman's fashion": ("Women's Modest Wear", "womens-modest-wear", None, None),
    "womans fashion": ("Women's Modest Wear", "womens-modest-wear", None, None),
    "woman clothing": ("Women's Modest Wear", "womens-modest-wear", None, None),
    "women's clothing": ("Women's Modest Wear", "womens-modest-wear", None, None),
    "woman khimar": ("Women's Modest Wear", "womens-modest-wear", "Woman Khimar", "woman-khimar"),
    "woman hijab": ("Women's Modest Wear", "womens-modest-wear", "Woman Hijab", "woman-hijab"),
    "woman borka": ("Women's Modest Wear", "womens-modest-wear", "Woman Borka", "woman-borka"),
    "woman jilbab": ("Women's Modest Wear", "womens-modest-wear", "Woman Jilbab", "woman-jilbab"),
    "salat hijab": ("Women's Modest Wear", "womens-modest-wear", "Salat Hijab", "salat-hijab"),
    "kid's fashion": ("Kids' Modest Wear", "kids-modest-wear", None, None),
    "kids fashion": ("Kids' Modest Wear", "kids-modest-wear", None, None),
    "kids clothing": ("Kids' Modest Wear", "kids-modest-wear", None, None),
    "kids khimar": ("Kids' Modest Wear", "kids-modest-wear", "Kids Khimar", "kids-khimar"),
    "kids hijab": ("Kids' Modest Wear", "kids-modest-wear", "Kids Hijab", "kids-hijab"),
    "kids borka": ("Kids' Modest Wear", "kids-modest-wear", "Kids Borka", "kids-borka"),
    "kids jubba": ("Kids' Modest Wear", "kids-modest-wear", "Kids Jubba", "kids-jubba"),
    "kids panjabi": ("Kids' Modest Wear", "kids-modest-wear", "Kids Panjabi", "kids-panjabi"),
    "electronices": ("Smart Gadgets", "smart-gadgets", None, None),
    "electronics": ("Smart Gadgets", "smart-gadgets", None, None),
    "gadget": ("Smart Gadgets", "smart-gadgets", None, None),
    "smart gadgets": ("Smart Gadgets", "smart-gadgets", None, None),
    "home & kitchen": ("Home & Kitchen", "home-kitchen", None, None),
    "kitchen": ("Home & Kitchen", "home-kitchen", "Kitchen Organizers", "kitchen-organizers"),
    "home tools": ("Home & Kitchen", "home-kitchen", "Home Tools", "home-tools"),
    "life style": ("Home & Kitchen", "home-kitchen", "Lifestyle & Personal Care", "life-style"),
    "all foods": ("Home & Kitchen", "home-kitchen", "Pantry & Organic Foods", "all-foods"),
    "home decor": ("Home Decor & Living", "home-decor", None, None),
    "metal items": ("Home Decor & Living", "home-decor", "Metal Items", "metal-items"),
}

# Droploo external category ID map (verified in docs/implementation-plan.md)
DEFAULT_DROPLOO_CATEGORY_IDS = {
    "48": ("Home Decor & Living", "home-decor"),
    "50": ("Smart Gadgets", "smart-gadgets"),
    "51": ("Home & Kitchen", "home-kitchen"),
    "52": ("Smart Gadgets", "smart-gadgets"),
    "53": ("Home Decor & Living", "home-decor"),
    "54": ("Home & Kitchen", "home-kitchen"),
    "55": ("Home & Kitchen", "home-kitchen"),
    "56": ("Men's Modest Wear", "mens-modest-wear"),
    "57": ("Women's Modest Wear", "womens-modest-wear"),
    "58": ("Kids' Modest Wear", "kids-modest-wear"),
}

ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def normalize_currency_code(raw_currency: Any, expected_currency: str = "BDT") -> str:
    """Validates and normalizes a currency code or symbol against the store's configured currency."""
    if raw_currency is None or str(raw_currency).strip() == "":
        return expected_currency
    token = str(raw_currency).strip().upper()
    resolved = CURRENCY_TOKEN_MAP.get(token)
    if not resolved:
        if len(token) == 3 and token.isalpha() and token in VALID_ISO_CURRENCIES:
            resolved = token
        else:
            raise ImportValidationError(
                "currency",
                f"Invalid or unrecognized currency code '{raw_currency}'. Expected '{expected_currency}'.",
            )
    if resolved != expected_currency:
        raise ImportValidationError(
            "currency",
            f"Currency '{resolved}' is not supported; this store requires '{expected_currency}'.",
        )
    return resolved


def parse_price_and_currency(
    raw_value: Any,
    field_name: str,
    row_currency_raw: Any = None,
    expected_currency: str = "BDT",
    allow_zero: bool = False,
) -> Decimal | None:
    """
    Parses a monetary value with explicit currency validation.
    Returns None if `raw_value` is None or empty string.
    Raises `ImportValidationError` on invalid decimals, negative numbers, or currency mismatch.
    """
    # Validate row-level currency first if provided
    row_currency = None
    if row_currency_raw is not None and str(row_currency_raw).strip() != "":
        raw_curr_token = str(row_currency_raw).strip().upper()
        row_currency = CURRENCY_TOKEN_MAP.get(raw_curr_token)
        if not row_currency:
            if len(raw_curr_token) == 3 and raw_curr_token.isalpha() and raw_curr_token in VALID_ISO_CURRENCIES:
                row_currency = raw_curr_token
            else:
                raise ImportValidationError(
                    "currency",
                    f"Invalid currency code '{row_currency_raw}'. Expected '{expected_currency}'.",
                )

    if raw_value is None:
        if row_currency and row_currency != expected_currency:
            raise ImportValidationError(
                "currency",
                f"Currency '{row_currency}' does not match store currency '{expected_currency}'.",
            )
        return None

    text = str(raw_value).strip()
    if text == "" or text.lower() == "null" or text.lower() == "none":
        if row_currency and row_currency != expected_currency:
            raise ImportValidationError(
                "currency",
                f"Currency '{row_currency}' does not match store currency '{expected_currency}'.",
            )
        return None

    # Check for embedded currency symbols or codes in the price string
    embedded_currency = None
    cleaned = text
    # Sort tokens longest first so "US$" / "TAKA" / "BDT" match before "$"
    for tok, iso_code in sorted(CURRENCY_TOKEN_MAP.items(), key=lambda x: -len(x[0])):
        pattern = rf"(?i)^\s*{re.escape(tok)}\s*|\s*{re.escape(tok)}\s*$"
        if re.search(pattern, cleaned):
            embedded_currency = iso_code
            cleaned = re.sub(pattern, "", cleaned).strip()
            break

    # Also check if there is any 3-letter prefix/suffix like "AUD 50" or "50 CAD"
    if embedded_currency is None:
        m_prefix = re.match(r"^([A-Za-z]{3})\s+(.+)$", cleaned)
        m_suffix = re.match(r"^(.+?)\s+([A-Za-z]{3})$", cleaned)
        if m_prefix:
            cand = m_prefix.group(1).upper()
            if cand in VALID_ISO_CURRENCIES:
                embedded_currency = cand
                cleaned = m_prefix.group(2).strip()
            else:
                raise ImportValidationError(
                    field_name,
                    f"Unrecognized currency prefix '{cand}' in {field_name} '{text}'.",
                )
        elif m_suffix:
            cand = m_suffix.group(2).upper()
            if cand in VALID_ISO_CURRENCIES:
                embedded_currency = cand
                cleaned = m_suffix.group(1).strip()
            else:
                raise ImportValidationError(
                    field_name,
                    f"Unrecognized currency suffix '{cand}' in {field_name} '{text}'.",
                )

    if embedded_currency and row_currency and embedded_currency != row_currency:
        raise ImportValidationError(
            field_name,
            f"Currency in {field_name} ('{embedded_currency}') conflicts with row currency ('{row_currency}').",
        )

    effective_currency = embedded_currency or row_currency or expected_currency
    if effective_currency != expected_currency:
        raise ImportValidationError(
            "currency" if row_currency and row_currency != expected_currency else field_name,
            f"Currency '{effective_currency}' is not allowed; store currency is '{expected_currency}'.",
        )

    # Remove thousands commas
    cleaned_num = cleaned.replace(",", "").strip()
    if not re.match(r"^-?\d+(\.\d+)?$", cleaned_num):
        raise ImportValidationError(
            field_name,
            f"Invalid numeric price value '{raw_value}' for {field_name}.",
        )

    try:
        dec = Decimal(cleaned_num)
    except InvalidOperation as exc:
        raise ImportValidationError(
            field_name,
            f"Invalid decimal value '{raw_value}' for {field_name}.",
        ) from exc

    if dec.is_nan() or dec.is_infinite():
        raise ImportValidationError(field_name, f"Non-finite price '{raw_value}' for {field_name}.")

    if dec < Decimal("0.00"):
        raise ImportValidationError(field_name, f"{field_name} cannot be negative ({dec}).")

    if not allow_zero and dec == Decimal("0.00"):
        raise ImportValidationError(field_name, f"{field_name} must be greater than zero.")

    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_optional_decimal_percentage(raw_value: Any, field_name: str) -> Decimal | None:
    """Parses an optional percentage value (e.g., '25', '25.5%')."""
    if raw_value is None:
        return None
    text = str(raw_value).strip().rstrip("%").strip()
    if not text or text.lower() in ("null", "none"):
        return None
    try:
        dec = Decimal(text)
    except InvalidOperation as exc:
        raise ImportValidationError(field_name, f"Invalid percentage '{raw_value}' for {field_name}.") from exc
    if dec.is_nan() or dec.is_infinite():
        raise ImportValidationError(field_name, f"Invalid percentage '{raw_value}' for {field_name}.")
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_optional_stock(raw_value: Any, field_name: str = "stock") -> tuple[bool, int]:
    """
    Parses an optional integer stock quantity.
    Returns `(supplied, stock_qty)`:
    - `(False, 0)` when the source did not supply stock (None or empty).
    - `(True, int_val)` when the source supplied a non-negative integer stock level.
    Raises `ImportValidationError` on negative or non-integer values.
    """
    if raw_value is None:
        return False, 0
    if isinstance(raw_value, bool):
        raise ImportValidationError(field_name, f"Invalid stock value '{raw_value}'.")
    text = str(raw_value).strip()
    if text == "" or text.lower() in ("null", "none"):
        return False, 0
    if not re.match(r"^-?\d+$", text):
        raise ImportValidationError(field_name, f"Stock quantity '{raw_value}' must be a whole integer.")
    val = int(text)
    if val < 0:
        raise ImportValidationError(field_name, f"Stock quantity cannot be negative ({val}).")
    return True, val


def sanitize_description_html(raw_html: str) -> str:
    """
    Sanitizes untrusted supplier HTML descriptions by removing executable/dangerous tags,
    inline event handlers, and javascript: URIs.
    """
    if not raw_html:
        return ""
    cleaned = str(raw_html).strip()
    # Remove script, style, iframe, object, embed, form blocks completely
    cleaned = re.sub(
        r"<\s*(script|style|iframe|object|embed|form|meta|link)\b[^>]*>.*?<\s*/\s*\1\s*>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"<\s*(script|style|iframe|object|embed|form|meta|link)\b[^>]*/?\s*>",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Strip inline on* attributes and javascript: URIs
    cleaned = re.sub(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\s+on[a-z]+\s*=\s*[^\s>]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"javascript\s*:", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def strip_html_to_text(raw_html: str) -> str:
    """Converts HTML description into clean plain text."""
    if not raw_html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", str(raw_html), flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


class SafeImageHandler:
    """
    Handles image URL validation and authorized-only image downloading with:
    - Environment-controlled authorization check (`IMPORT_ALLOW_IMAGE_DOWNLOAD`)
    - Host allowlisting (`IMPORT_ALLOWED_IMAGE_HOSTS`) and private/loopback IP blocking (SSRF protection)
    - Strict request timeouts (`IMPORT_IMAGE_TIMEOUT_SECONDS`)
    - File-size limits (`IMPORT_MAX_IMAGE_BYTES`)
    - Content-Type header + image magic-byte verification
    - Safe, sanitized deterministic filenames
    """

    @staticmethod
    def normalize_image_reference(raw_url: str) -> str | None:
        """
        Validates and normalizes a local static/media path or external HTTP(S) image URL.
        Encodes unescaped spaces in paths (common in Droploo gallery URLs).
        Returns None if the reference is invalid or uses a disallowed scheme.
        """
        if not raw_url:
            return None
        val = str(raw_url).strip()
        if not val or val.lower() in ("null", "none"):
            return None

        # Local static or media path
        if val.startswith("/static/") or val.startswith("/media/"):
            if ".." in val or "\x00" in val:
                return None
            unquoted_rel = urllib.parse.unquote(val).lstrip("/")
            if val.startswith("/static/"):
                candidate_path = Path(settings.BASE_DIR) / unquoted_rel
            else:
                rel_media = unquoted_rel[len("media/") :] if unquoted_rel.startswith("media/") else unquoted_rel
                candidate_path = Path(settings.MEDIA_ROOT) / rel_media
            if not candidate_path.exists() or not candidate_path.is_file():
                return None
            parsed_path = urllib.parse.quote(val, safe="/:._-~")
            return parsed_path

        parsed = urllib.parse.urlsplit(val)
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        if not parsed.netloc or not parsed.hostname:
            return None
        if "." not in parsed.hostname and parsed.hostname.lower() != "localhost":
            return None

        # Encode raw spaces and unsafe characters in URL path
        safe_path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/:._-~@!$&'()*+,;=")
        safe_query = urllib.parse.quote_plus(parsed.query, safe="=&:._-~")
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, safe_path, safe_query, "")
        )

    @staticmethod
    def _is_Disallowed_ip_or_host(hostname: str) -> tuple[bool, str]:
        """Checks host allowlist and blocks private, loopback, link-local, or reserved IPs."""
        host_lower = (hostname or "").strip().lower()
        if not host_lower:
            return True, "Missing hostname in image URL."

        if host_lower in ("localhost", "localhost.localdomain"):
            return True, f"Loopback host '{host_lower}' is not permitted for image downloads."

        allowed_hosts = getattr(settings, "IMPORT_ALLOWED_IMAGE_HOSTS", [])
        if allowed_hosts:
            matched = any(
                host_lower == allowed or host_lower.endswith(f".{allowed}")
                for allowed in allowed_hosts
            )
            if not matched:
                return (
                    True,
                    f"Image host '{host_lower}' is not in IMPORT_ALLOWED_IMAGE_HOSTS ({', '.join(allowed_hosts)}).",
                )

        # Check if hostname is a literal IP address
        try:
            ip_obj = ipaddress.ip_address(host_lower)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
                or ip_obj.is_unspecified
            ):
                return True, f"Private or reserved IP address '{host_lower}' is blocked."
        except ValueError:
            pass

        return False, ""

    @staticmethod
    def _verify_magic_bytes(data: bytes) -> str | None:
        """Verifies actual image file signature (magic bytes) and returns canonical MIME type."""
        if len(data) < 12:
            return None
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    @staticmethod
    def build_safe_filename(product_sku: str, source_url: str, ext: str) -> str:
        """Generates a traversal-safe, lowercase alphanumeric filename with a short URL hash."""
        parsed = urllib.parse.urlsplit(source_url)
        raw_basename = os.path.basename(urllib.parse.unquote(parsed.path))
        stem, _ = os.path.splitext(raw_basename)
        safe_sku = re.sub(r"[^a-z0-9_-]", "-", (product_sku or "product").lower()).strip("-")[:24] or "prod"
        safe_stem = re.sub(r"[^a-z0-9_-]", "-", (stem or "img").lower()).strip("-")[:32] or "img"
        url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:8]
        clean_ext = ext if ext.startswith(".") else f".{ext}"
        return f"{safe_sku}-{safe_stem}-{url_hash}{clean_ext}"

    @classmethod
    def download_authorized_image(
        cls,
        url: str,
        product_sku: str,
    ) -> tuple[str | None, bytes | None, str | None]:
        """
        Downloads an image only when authorized by settings.IMPORT_ALLOW_IMAGE_DOWNLOAD.
        Returns `(safe_filename, content_bytes, error_message)`.
        """
        if not getattr(settings, "IMPORT_ALLOW_IMAGE_DOWNLOAD", False):
            return (
                None,
                None,
                "Image downloading is disabled (set IMPORT_ALLOW_IMAGE_DOWNLOAD=True in environment to authorize).",
            )

        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return None, None, f"Unsupported URL scheme '{parsed.scheme}' for image download."

        is_blocked, block_reason = cls._is_Disallowed_ip_or_host(parsed.hostname or "")
        if is_blocked:
            return None, None, block_reason

        timeout_sec = int(getattr(settings, "IMPORT_IMAGE_TIMEOUT_SECONDS", 10))
        max_bytes = int(getattr(settings, "IMPORT_MAX_IMAGE_BYTES", 5 * 1024 * 1024))

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "DeenyaMart-CatalogImporter/1.0",
                "Accept": "image/jpeg,image/png,image/webp,image/gif",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                status_code = getattr(resp, "status", 200)
                if status_code and int(status_code) >= 400:
                    return None, None, f"HTTP {status_code} returned for image URL '{url}'."

                headers = getattr(resp, "headers", {}) or {}
                raw_ctype = headers.get("Content-Type", "") if hasattr(headers, "get") else ""
                content_type = raw_ctype.split(";")[0].strip().lower()
                if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
                    return (
                        None,
                        None,
                        f"Disallowed Content-Type '{content_type or 'unknown'}' for image URL '{url}'.",
                    )

                content_length_hdr = headers.get("Content-Length") if hasattr(headers, "get") else None
                if content_length_hdr:
                    try:
                        if int(content_length_hdr) > max_bytes:
                            return (
                                None,
                                None,
                                f"Image Content-Length ({content_length_hdr} bytes) exceeds limit of {max_bytes} bytes.",
                            )
                    except ValueError:
                        pass

                chunks = []
                total_read = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total_read += len(chunk)
                    if total_read > max_bytes:
                        return (
                            None,
                            None,
                            f"Downloaded image stream exceeded max file size limit of {max_bytes} bytes.",
                        )
                    chunks.append(chunk)

                content_bytes = b"".join(chunks)
                if not content_bytes:
                    return None, None, f"Empty image response from '{url}'."

                detected_mime = cls._verify_magic_bytes(content_bytes)
                if not detected_mime or detected_mime not in ALLOWED_IMAGE_CONTENT_TYPES:
                    return (
                        None,
                        None,
                        f"File signature verification failed for '{url}'; payload is not a valid JPEG/PNG/WebP/GIF image.",
                    )

                ext = ALLOWED_IMAGE_CONTENT_TYPES[detected_mime]
                safe_name = cls.build_safe_filename(product_sku, url, ext)
                return safe_name, content_bytes, None

        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            return None, None, f"Failed to download image '{url}': {exc}"


class CategoryResolver:
    """Resolves supplier category IDs, names, slugs, and parent/child paths to local `Category` instances."""

    @classmethod
    def resolve(
        cls,
        source_name: str,
        raw_category: str = "",
        raw_subcategory: str = "",
        ext_cat_id: str = "",
        ext_subcat_id: str = "",
        strict_categories: bool = False,
        default_category_slug: str | None = None,
        existing_product: Product | None = None,
        overwrite_blank: bool = False,
    ) -> Category:
        cat_text = (raw_category or "").strip()
        subcat_text = (raw_subcategory or "").strip()
        ext_cid = str(ext_cat_id or "").strip()
        ext_scid = str(ext_subcat_id or "").strip()

        # Support "Parent > Child" or "Parent / Child" syntax in raw_category
        if not subcat_text and (">" in cat_text or "/" in cat_text):
            sep = ">" if ">" in cat_text else "/"
            parts = [p.strip() for p in cat_text.split(sep, 1) if p.strip()]
            if len(parts) == 2:
                cat_text, subcat_text = parts[0], parts[1]

        # 1. Check SupplierCategoryMap in database
        if ext_cid or cat_text or subcat_text:
            qs = SupplierCategoryMap.objects.select_related("mapped_category").filter(
                source_name__iexact=source_name
            )
            if ext_cid and ext_scid:
                mapped = qs.filter(
                    external_category_id=ext_cid,
                    external_subcategory_id=ext_scid,
                ).first()
                if mapped:
                    return mapped.mapped_category
            if ext_cid:
                mapped = qs.filter(external_category_id=ext_cid, external_subcategory_id="").first()
                if mapped and not subcat_text:
                    return mapped.mapped_category
            if subcat_text:
                mapped = qs.filter(
                    Q(external_name__iexact=subcat_text) | Q(external_slug__iexact=slugify(subcat_text))
                ).first()
                if mapped:
                    return mapped.mapped_category
            if cat_text:
                mapped = qs.filter(
                    Q(external_name__iexact=cat_text) | Q(external_slug__iexact=slugify(cat_text))
                ).first()
                if mapped and not subcat_text:
                    return mapped.mapped_category

        # 2. Check built-in supplier alias map (normalizing typos like "Man's Cothing")
        lookup_key = (subcat_text or cat_text).lower()
        if lookup_key in DEFAULT_SUPPLIER_CATEGORY_ALIASES:
            p_name, p_slug, c_name, c_slug = DEFAULT_SUPPLIER_CATEGORY_ALIASES[lookup_key]
            existing_target = Category.objects.filter(slug=c_slug or p_slug).first()
            if existing_target:
                return existing_target
            if not strict_categories:
                parent_cat, _ = Category.objects.get_or_create(
                    slug=p_slug,
                    defaults={"name": p_name, "is_active": True},
                )
                if c_name and c_slug:
                    child_cat, _ = Category.objects.get_or_create(
                        slug=c_slug,
                        defaults={"name": c_name, "parent": parent_cat, "is_active": True},
                    )
                    return child_cat
                return parent_cat

        # 3. Check built-in Droploo numeric category IDs
        if not cat_text and not subcat_text and ext_cid in DEFAULT_DROPLOO_CATEGORY_IDS:
            p_name, p_slug = DEFAULT_DROPLOO_CATEGORY_IDS[ext_cid]
            existing_target = Category.objects.filter(slug=p_slug).first()
            if existing_target:
                return existing_target
            if not strict_categories:
                parent_cat, _ = Category.objects.get_or_create(
                    slug=p_slug,
                    defaults={"name": p_name, "is_active": True},
                )
                return parent_cat

        # 4. Direct match against existing Category by slug or name
        if subcat_text:
            sub_slug = slugify(subcat_text)
            direct_sub = Category.objects.filter(
                Q(slug=sub_slug) | Q(name__iexact=subcat_text)
            ).first()
            if direct_sub:
                return direct_sub

        if cat_text:
            cat_slug = slugify(cat_text)
            direct_cat = Category.objects.filter(
                Q(slug=cat_slug) | Q(name__iexact=cat_text)
            ).first()
            if direct_cat and not subcat_text:
                return direct_cat
            if direct_cat and subcat_text:
                if strict_categories:
                    raise ImportValidationError(
                        "category",
                        f"Unmapped subcategory '{subcat_text}' under '{cat_text}' (strict_categories=True).",
                    )
                sub_slug = slugify(subcat_text) or "subcategory"
                child_cat, _ = Category.objects.get_or_create(
                    slug=sub_slug,
                    defaults={"name": subcat_text, "parent": direct_cat, "is_active": True},
                )
                return child_cat

        # 5. Create new Category if allowed and category name was provided
        if cat_text:
            if strict_categories:
                raise ImportValidationError(
                    "category",
                    f"Category '{cat_text}' does not exist and is not mapped in SupplierCategoryMap.",
                )
            parent_slug = slugify(cat_text) or "category"
            parent_cat, _ = Category.objects.get_or_create(
                slug=parent_slug,
                defaults={"name": cat_text, "is_active": True},
            )
            if subcat_text:
                sub_slug = slugify(subcat_text) or "subcategory"
                child_cat, _ = Category.objects.get_or_create(
                    slug=sub_slug,
                    defaults={"name": subcat_text, "parent": parent_cat, "is_active": True},
                )
                return child_cat
            return parent_cat

        # 6. Absent category in source: preserve existing product category on update
        if existing_product is not None and existing_product.category_id and not overwrite_blank:
            return existing_product.category

        # 7. Optional CLI fallback category slug
        if default_category_slug:
            fallback = Category.objects.filter(slug=default_category_slug).first()
            if fallback:
                return fallback

        raise ImportValidationError(
            "category",
            "Missing required field 'category' (and no matching SupplierCategoryMap found).",
        )


class ProductImportPipeline:
    """
    Core service executing CSV and JSON product imports with row-level savepoints,
    idempotent upserts, duplicate detection, and audit logging.
    """

    def __init__(self, options: ImportOptions | None = None):
        self.options = options or ImportOptions()
        self.store_currency = getattr(settings, "STORE_CURRENCY_CODE", "BDT")

    def run_file_import(self, file_path: str | Path, input_format: str = "auto") -> ImportJobLog:
        path = Path(file_path)
        started_at = timezone.now()

        fmt = (input_format or "auto").strip().lower()
        if fmt == "auto":
            if path.suffix.lower() == ".json":
                fmt = "json"
            elif path.suffix.lower() == ".csv":
                fmt = "csv"
            else:
                fmt = "csv"

        source_type = "JSON_FILE" if fmt == "json" else "CSV_FILE"
        job_log = ImportJobLog.objects.create(
            source_name=self.options.source_name,
            source_type=source_type,
            source_reference=str(path),
            status=ImportJobLog.Status.RUNNING,
            is_dry_run=self.options.dry_run,
            overwrite_blank=self.options.overwrite_blank,
            download_images=self.options.download_images,
            started_at=started_at,
        )

        if not path.exists() or not path.is_file():
            job_log.status = ImportJobLog.Status.FAILED
            job_log.error_count = 1
            job_log.row_errors = [
                {
                    "row": 0,
                    "external_id": "",
                    "sku": "",
                    "field": "file",
                    "error": f"Import file not found: {path}",
                }
            ]
            job_log.finished_at = timezone.now()
            job_log.save()
            return job_log

        try:
            records = self._load_records(path, fmt)
        except Exception as exc:
            job_log.status = ImportJobLog.Status.FAILED
            job_log.error_count = 1
            job_log.row_errors = [
                {
                    "row": 0,
                    "external_id": "",
                    "sku": "",
                    "field": "file",
                    "error": f"Failed to parse {fmt.upper()} file: {exc}",
                }
            ]
            job_log.finished_at = timezone.now()
            job_log.save()
            return job_log

        return self._process_records(records, job_log)

    def _load_records(self, path: Path, fmt: str) -> list[tuple[int, dict[str, Any]]]:
        """Reads CSV or JSON file into a list of `(row_number, raw_dict)` tuples."""
        if fmt == "json":
            raw_text = path.read_text(encoding="utf-8-sig")
            payload = json.loads(raw_text)
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                for key in ("products", "data", "items", "records"):
                    if isinstance(payload.get(key), list):
                        items = payload[key]
                        break
                else:
                    raise ValueError("JSON root object must contain a 'products', 'data', or 'items' array.")
            else:
                raise ValueError("JSON input must be an array of product objects or an object containing 'products'.")

            result = []
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    raise ValueError(f"Record #{idx} in JSON is not an object.")
                result.append((idx, item))
            return result

        # CSV parser
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV file is empty or missing a header row.")
            result = []
            # Row 1 is the header; data rows start at row 2
            for row_num, row in enumerate(reader, start=2):
                normalized_row = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    norm_key = k.strip().lower().replace(" ", "_").replace("-", "_")
                    normalized_row[norm_key] = v.strip() if isinstance(v, str) else v
                # Skip completely empty trailing lines
                if all(v in (None, "") for v in normalized_row.values()):
                    continue
                result.append((row_num, normalized_row))
            return result

    def _process_records(
        self,
        records: list[tuple[int, dict[str, Any]]],
        job_log: ImportJobLog,
    ) -> ImportJobLog:
        total_records = len(records)
        created_count = 0
        updated_count = 0
        unchanged_count = 0
        skipped_count = 0
        duplicate_count = 0
        warning_count = 0
        error_count = 0
        row_errors: list[dict[str, Any]] = []
        row_warnings: list[dict[str, Any]] = []

        # Track in-file duplicates
        seen_external_ids: dict[str, int] = {}
        seen_product_skus: dict[str, int] = {}
        seen_variant_skus: dict[str, int] = {}

        def _execute_all_rows():
            nonlocal created_count, updated_count, unchanged_count, skipped_count
            nonlocal duplicate_count, warning_count, error_count

            for row_num, raw_record in records:
                norm = self._normalize_keys(raw_record)
                raw_ext_id = self._extract_first(
                    norm, ("external_id", "external_product_id", "source_id", "id")
                )
                raw_sku = self._extract_first(norm, ("sku", "sku_prefix", "product_code"))
                ext_id_str = str(raw_ext_id).strip() if raw_ext_id not in (None, "") else ""
                sku_str = str(raw_sku).strip() if raw_sku not in (None, "") else ""

                try:
                    with transaction.atomic():
                        outcome, warnings = self._import_single_record(
                            row_num=row_num,
                            norm=norm,
                            raw_record=raw_record,
                            seen_external_ids=seen_external_ids,
                            seen_product_skus=seen_product_skus,
                            seen_variant_skus=seen_variant_skus,
                        )
                        for w in warnings:
                            row_warnings.append(w)
                            warning_count += 1

                        if outcome == "created":
                            created_count += 1
                        elif outcome == "updated":
                            updated_count += 1
                        elif outcome == "unchanged":
                            updated_count += 1
                            unchanged_count += 1
                except ImportValidationError as exc:
                    error_count += 1
                    if exc.is_duplicate:
                        duplicate_count += 1
                    row_errors.append(
                        {
                            "row": row_num,
                            "external_id": ext_id_str,
                            "sku": sku_str,
                            "field": exc.field_name,
                            "error": exc.message,
                        }
                    )
                except (ValidationError, IntegrityError) as exc:
                    error_count += 1
                    msg = str(exc.message_dict if hasattr(exc, "message_dict") else exc)
                    row_errors.append(
                        {
                            "row": row_num,
                            "external_id": ext_id_str,
                            "sku": sku_str,
                            "field": "model",
                            "error": msg,
                        }
                    )
                except Exception as exc:
                    error_count += 1
                    row_errors.append(
                        {
                            "row": row_num,
                            "external_id": ext_id_str,
                            "sku": sku_str,
                            "field": "unexpected",
                            "error": str(exc),
                        }
                    )

        if self.options.dry_run:
            # Run inside an outer atomic savepoint and force rollback so zero catalog changes persist
            with transaction.atomic():
                _execute_all_rows()
                transaction.set_rollback(True)
            final_status = ImportJobLog.Status.DRY_RUN
        else:
            _execute_all_rows()
            if error_count == 0:
                final_status = ImportJobLog.Status.COMPLETED
            elif created_count > 0 or updated_count > 0 or unchanged_count > 0:
                final_status = ImportJobLog.Status.COMPLETED_WITH_ERRORS
            else:
                final_status = ImportJobLog.Status.FAILED

        finished_at = timezone.now()
        job_log.total_records = total_records
        job_log.created_count = created_count
        job_log.updated_count = updated_count
        job_log.unchanged_count = unchanged_count
        job_log.skipped_count = skipped_count
        job_log.duplicate_count = duplicate_count
        job_log.warning_count = warning_count
        job_log.error_count = error_count
        job_log.row_errors = row_errors
        job_log.row_warnings = row_warnings
        job_log.status = final_status
        job_log.finished_at = finished_at
        job_log.summary_data = {
            "source_name": self.options.source_name,
            "dry_run": self.options.dry_run,
            "overwrite_blank": self.options.overwrite_blank,
            "download_images": self.options.download_images,
            "total_records": total_records,
            "created_count": created_count,
            "updated_count": updated_count,
            "unchanged_count": unchanged_count,
            "skipped_count": skipped_count,
            "duplicate_count": duplicate_count,
            "warning_count": warning_count,
            "error_count": error_count,
            "duration_seconds": round((finished_at - job_log.started_at).total_seconds(), 3),
        }
        job_log.save()
        return job_log

    @staticmethod
    def _normalize_keys(raw_record: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in raw_record.items():
            if k is None:
                continue
            nk = str(k).strip().lower().replace(" ", "_").replace("-", "_")
            out[nk] = v
        return out

    @staticmethod
    def _extract_first(norm: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for k in keys:
            if k in norm and norm[k] is not None and str(norm[k]).strip() != "":
                return norm[k]
        return None

    @staticmethod
    def _is_field_present_in_source(norm: dict[str, Any], keys: tuple[str, ...]) -> bool:
        return any(k in norm for k in keys)

    def _import_single_record(
        self,
        row_num: int,
        norm: dict[str, Any],
        raw_record: dict[str, Any],
        seen_external_ids: dict[str, int],
        seen_product_skus: dict[str, int],
        seen_variant_skus: dict[str, int],
    ) -> tuple[str, list[dict[str, Any]]]:
        warnings: list[dict[str, Any]] = []
        source_name = self.options.source_name

        # 1. Extract and validate stable identifiers (external_id and/or sku)
        raw_ext_id = self._extract_first(
            norm, ("external_id", "external_product_id", "source_id", "id")
        )
        raw_sku = self._extract_first(norm, ("sku", "sku_prefix", "product_code"))
        raw_name = self._extract_first(norm, ("name", "product_name", "title"))

        ext_id = str(raw_ext_id).strip() if raw_ext_id not in (None, "") else ""
        sku = str(raw_sku).strip() if raw_sku not in (None, "") else ""

        # If SKU is missing but name ends with a style code or external_id exists, derive SKU only if needed
        if not ext_id and not sku:
            raise ImportValidationError(
                "external_id",
                "Missing stable identifier: each record must provide 'external_id' or 'sku'.",
            )

        if not ext_id:
            ext_id = f"SKU:{sku.upper()}"

        # Check in-file duplicate external_id
        ext_id_key = ext_id.upper()
        if ext_id_key in seen_external_ids:
            first_row = seen_external_ids[ext_id_key]
            raise ImportValidationError(
                "external_id",
                f"Duplicate external_id '{ext_id}' in import file (already appeared on row {first_row}).",
                is_duplicate=True,
            )

        # Look up existing ProductSource / SupplierProductMap for idempotent upsert
        existing_source = (
            ProductSource.objects.select_related("product", "product__category", "product__brand")
            .filter(source_name=source_name, external_id=ext_id)
            .first()
        )
        existing_map = (
            SupplierProductMap.objects.select_related("product", "product__category", "product__brand")
            .filter(source_name=source_name, external_product_id=ext_id)
            .first()
        )
        existing_product: Product | None = None
        if existing_source:
            existing_product = existing_source.product
        elif existing_map:
            existing_product = existing_map.product

        # Resolve SKU
        if not sku:
            if existing_product and existing_product.sku:
                sku = existing_product.sku
            else:
                # Derive deterministic SKU from external_id
                clean_ext = re.sub(r"[^A-Za-z0-9_-]", "", ext_id).upper()[:40] or "ITEM"
                sku = f"DRP-{clean_ext}" if not clean_ext.startswith("DRP-") else clean_ext

        sku_upper = sku.upper()
        if sku_upper in seen_product_skus:
            first_row = seen_product_skus[sku_upper]
            raise ImportValidationError(
                "sku",
                f"Duplicate SKU '{sku}' in import file (already used on row {first_row}).",
                is_duplicate=True,
            )
        if sku_upper in seen_variant_skus:
            first_row = seen_variant_skus[sku_upper]
            raise ImportValidationError(
                "sku",
                f"SKU '{sku}' conflicts with a variant SKU on row {first_row}.",
                is_duplicate=True,
            )

        # Check DB for SKU conflicts
        sku_conflict_qs = Product.objects.filter(Q(sku__iexact=sku) | Q(sku_prefix__iexact=sku))
        if existing_product:
            sku_conflict_qs = sku_conflict_qs.exclude(pk=existing_product.pk)

        conflicting_prod = sku_conflict_qs.first()
        if conflicting_prod:
            # If existing_product was not found by external_id, check if conflicting_prod is a development
            # fixture that is being replaced by its real external_id, or if it's a genuine conflict
            if existing_product is None and conflicting_prod.is_development_fixture:
                existing_product = conflicting_prod
                existing_source = conflicting_prod.sources.filter(source_name=source_name).first()
            else:
                raise ImportValidationError(
                    "sku",
                    f"Duplicate SKU conflict: SKU '{sku}' is already assigned to existing product '{conflicting_prod.name}' (ID {conflicting_prod.pk}).",
                    is_duplicate=True,
                )

        # Also check if any other product's variant already uses this product SKU
        var_conflict_qs = ProductVariant.objects.filter(sku__iexact=sku)
        if existing_product:
            var_conflict_qs = var_conflict_qs.exclude(product=existing_product)
        if var_conflict_qs.exists():
            raise ImportValidationError(
                "sku",
                f"Duplicate SKU conflict: SKU '{sku}' is already assigned to a variant in the catalog.",
                is_duplicate=True,
            )

        # 2. Validate Product Name
        name_str = str(raw_name).strip() if raw_name not in (None, "") else ""
        if not name_str:
            if existing_product and not self.options.overwrite_blank:
                name_str = existing_product.name
            else:
                raise ImportValidationError("name", "Missing required field: 'name'.")

        # 3. Validate Currency & Prices (keeping supplier cost, retail selling price, and margin separate!)
        raw_currency = self._extract_first(norm, ("currency", "currency_code"))
        validated_currency = normalize_currency_code(raw_currency, expected_currency=self.store_currency)

        raw_supplier_cost = self._extract_first(
            norm, ("supplier_cost", "wholesale_price", "buy_price", "cost_price", "supplier_price")
        )
        raw_selling_price = self._extract_first(
            norm, ("selling_price", "retail_price", "customer_price", "store_price", "price")
        )
        raw_compare_at_price = self._extract_first(
            norm, ("compare_at_price", "original_price", "strikethrough_price", "mrp")
        )
        raw_supplier_suggested = self._extract_first(
            norm, ("supplier_regular_price", "regular_price", "suggested_retail_price")
        )

        supplier_cost = parse_price_and_currency(
            raw_supplier_cost,
            field_name="supplier_cost",
            row_currency_raw=raw_currency,
            expected_currency=self.store_currency,
            allow_zero=True,
        )
        # In Droploo exports, wholesale_price == 0 means unset/anomalous
        if supplier_cost == Decimal("0.00") and "supplier_cost" not in norm:
            supplier_cost = None

        selling_price = parse_price_and_currency(
            raw_selling_price,
            field_name="selling_price",
            row_currency_raw=raw_currency,
            expected_currency=self.store_currency,
            allow_zero=False,
        )

        compare_at_price = parse_price_and_currency(
            raw_compare_at_price,
            field_name="compare_at_price",
            row_currency_raw=raw_currency,
            expected_currency=self.store_currency,
            allow_zero=True,
        )

        supplier_suggested_price = parse_price_and_currency(
            raw_supplier_suggested,
            field_name="supplier_regular_price",
            row_currency_raw=raw_currency,
            expected_currency=self.store_currency,
            allow_zero=True,
        )

        row_margin_pct = parse_optional_decimal_percentage(
            self._extract_first(norm, ("margin_percent", "margin_percentage")),
            "margin_percent",
        )
        row_markup_pct = parse_optional_decimal_percentage(
            self._extract_first(norm, ("markup_percent", "markup_percentage")),
            "markup_percent",
        )

        effective_supplier_cost, effective_selling_price, effective_compare_at_price = (
            self._resolve_product_pricing(
                supplier_cost=supplier_cost,
                selling_price=selling_price,
                compare_at_price=compare_at_price,
                row_margin_pct=row_margin_pct,
                row_markup_pct=row_markup_pct,
                existing_product=existing_product,
            )
        )

        # 4. Resolve Category
        raw_cat = self._extract_first(norm, ("category", "category_name", "category_slug"))
        if isinstance(raw_cat, dict):
            raw_cat = raw_cat.get("name") or raw_cat.get("slug") or ""
        raw_subcat = self._extract_first(
            norm, ("subcategory", "subcategory_name", "subcategory_slug")
        )
        if isinstance(raw_subcat, dict):
            raw_subcat = raw_subcat.get("name") or raw_subcat.get("slug") or ""
        raw_ext_cat_id = self._extract_first(norm, ("external_category_id", "cat_id"))
        raw_ext_subcat_id = self._extract_first(norm, ("external_subcategory_id", "sub_cat_id"))

        category = CategoryResolver.resolve(
            source_name=source_name,
            raw_category=str(raw_cat or ""),
            raw_subcategory=str(raw_subcat or ""),
            ext_cat_id=str(raw_ext_cat_id or ""),
            ext_subcat_id=str(raw_ext_subcat_id or ""),
            strict_categories=self.options.strict_categories,
            default_category_slug=self.options.default_category_slug,
            existing_product=existing_product,
            overwrite_blank=self.options.overwrite_blank,
        )

        # 5. Resolve Optional Brand
        raw_brand = self._extract_first(norm, ("brand", "brand_name", "fabric"))
        if isinstance(raw_brand, dict):
            raw_brand = raw_brand.get("name") or ""
        brand_str = str(raw_brand).strip() if raw_brand not in (None, "") else ""
        if brand_str:
            brand_slug = slugify(brand_str) or "brand"
            brand = Brand.objects.filter(Q(slug=brand_slug) | Q(name__iexact=brand_str)).first()
            if not brand:
                brand = Brand.objects.create(name=brand_str, slug=brand_slug, is_active=True)
        elif existing_product and not self.options.overwrite_blank:
            brand = existing_product.brand
        else:
            brand = None

        # 6. Resolve Descriptions & Optional Attributes (non-destructive when blank!)
        raw_short_desc = self._extract_first(norm, ("short_description", "summary"))
        raw_long_desc = self._extract_first(
            norm, ("description", "long_description", "description_html")
        )

        short_desc_clean = (
            strip_html_to_text(str(raw_short_desc)) if raw_short_desc not in (None, "") else ""
        )
        long_desc_raw = str(raw_long_desc).strip() if raw_long_desc not in (None, "") else ""
        has_html_tags = bool(re.search(r"<[a-zA-Z][^>]*>", long_desc_raw))
        desc_html_clean = sanitize_description_html(long_desc_raw) if has_html_tags else ""
        desc_text_clean = strip_html_to_text(long_desc_raw) if long_desc_raw else ""

        if not short_desc_clean and desc_text_clean:
            short_desc_clean = desc_text_clean[:280]
        if not desc_text_clean and short_desc_clean:
            desc_text_clean = short_desc_clean

        if existing_product and not self.options.overwrite_blank:
            final_description = desc_text_clean if desc_text_clean else existing_product.description
            final_short_description = (
                short_desc_clean if short_desc_clean else existing_product.short_description
            )
            final_description_html = (
                desc_html_clean if desc_html_clean else existing_product.description_html
            )
        else:
            final_description = desc_text_clean
            final_short_description = short_desc_clean
            final_description_html = desc_html_clean

        raw_fabric_comp = self._extract_first(norm, ("fabric_composition", "material"))
        fabric_comp_str = str(raw_fabric_comp).strip() if raw_fabric_comp not in (None, "") else ""
        if not fabric_comp_str and existing_product and not self.options.overwrite_blank:
            fabric_comp_str = existing_product.fabric_composition

        raw_ptype = str(self._extract_first(norm, ("product_type",)) or "").strip().lower()
        raw_is_featured = self._extract_first(norm, ("is_featured",))
        raw_is_new = self._extract_first(norm, ("is_new_arrival",))
        raw_is_hot = self._extract_first(norm, ("is_hot_deal",))

        def _parse_bool(val, fallback: bool = False) -> bool:
            if val is None or str(val).strip() == "":
                return fallback
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("1", "true", "yes", "y")

        is_featured_val = _parse_bool(
            raw_is_featured,
            fallback=(raw_ptype == "feature") or (existing_product.is_featured if existing_product else False),
        )
        is_new_arrival_val = _parse_bool(
            raw_is_new,
            fallback=(raw_ptype == "new") or (existing_product.is_new_arrival if existing_product else False),
        )
        is_hot_deal_val = _parse_bool(
            raw_is_hot,
            fallback=(raw_ptype in ("hot", "discount")) or (existing_product.is_hot_deal if existing_product else False),
        )

        raw_img_fit = str(self._extract_first(norm, ("image_fit",)) or "").strip().lower()
        if raw_img_fit in ("cover", "contain"):
            image_fit_val = raw_img_fit
        elif existing_product:
            image_fit_val = existing_product.image_fit
        else:
            image_fit_val = "cover"

        raw_sort_order = self._extract_first(norm, ("sort_order", "priority"))
        try:
            sort_order_val = max(0, int(str(raw_sort_order).strip())) if raw_sort_order not in (None, "") else (existing_product.sort_order if existing_product else 0)
        except ValueError:
            sort_order_val = existing_product.sort_order if existing_product else 0

        inside_dhaka_val = parse_price_and_currency(
            self._extract_first(norm, ("inside_dhaka_shipping_override", "inside_dhaka")),
            field_name="inside_dhaka",
            row_currency_raw=raw_currency,
            expected_currency=self.store_currency,
            allow_zero=True,
        )
        if inside_dhaka_val == Decimal("0.00"):
            inside_dhaka_val = None
        elif inside_dhaka_val is None and existing_product and not self.options.overwrite_blank:
            inside_dhaka_val = existing_product.inside_dhaka_shipping_override

        outside_dhaka_val = parse_price_and_currency(
            self._extract_first(norm, ("outside_dhaka_shipping_override", "outside_dhaka")),
            field_name="outside_dhaka",
            row_currency_raw=raw_currency,
            expected_currency=self.store_currency,
            allow_zero=True,
        )
        if outside_dhaka_val == Decimal("0.00"):
            outside_dhaka_val = None
        elif outside_dhaka_val is None and existing_product and not self.options.overwrite_blank:
            outside_dhaka_val = existing_product.outside_dhaka_shipping_override

        # 7. Parse Variants & Stock (only where the source actually supplies them!)
        variants_supplied, parsed_variants = self._parse_variants(
            norm=norm,
            product_sku=sku,
            product_selling_price=effective_selling_price,
            product_compare_at_price=effective_compare_at_price,
            product_supplier_cost=effective_supplier_cost,
            row_currency=raw_currency,
            existing_product=existing_product,
            seen_product_skus=seen_product_skus,
            seen_variant_skus=seen_variant_skus,
            row_num=row_num,
        )

        raw_stock = self._extract_first(norm, ("stock", "stock_qty", "stock_quantity", "qty"))
        stock_supplied, product_stock_qty = parse_optional_stock(raw_stock, field_name="stock")

        # 8. Parse & Validate Images (only where the source actually supplies them!)
        images_supplied, parsed_images, img_warnings = self._parse_and_process_images(
            norm=norm,
            product_sku=sku,
            product_name=name_str,
            row_num=row_num,
            ext_id=ext_id,
        )
        warnings.extend(img_warnings)

        # Register identifiers in in-file duplicate trackers now that validation passed
        seen_external_ids[ext_id_key] = row_num
        seen_product_skus[sku_upper] = row_num
        for v_spec in parsed_variants:
            seen_variant_skus[v_spec["sku"].upper()] = row_num

        # 9. Compute canonical payload digest for idempotent change detection
        canonical_state = {
            "name": name_str,
            "sku": sku,
            "category_id": category.pk,
            "brand_id": brand.pk if brand else None,
            "currency": validated_currency,
            "selling_price": str(effective_selling_price),
            "compare_at_price": str(effective_compare_at_price) if effective_compare_at_price else None,
            "supplier_cost": str(effective_supplier_cost) if effective_supplier_cost is not None else None,
            "description": final_description,
            "short_description": final_short_description,
            "description_html": final_description_html,
            "fabric_composition": fabric_comp_str,
            "stock_supplied": stock_supplied,
            "product_stock_qty": product_stock_qty if stock_supplied else None,
            "variants": [
                {
                    "sku": v["sku"],
                    "size_label": v["size_label"],
                    "color_label": v["color_label"],
                    "option_name": v["option_name"],
                    "option_value": v["option_value"],
                    "retail_price": str(v["retail_price"]),
                    "compare_at_price": str(v["compare_at_price"]) if v["compare_at_price"] else None,
                    "wholesale_price": str(v["wholesale_price"]) if v["wholesale_price"] is not None else None,
                    "stock_supplied": v["stock_supplied"],
                    "stock_qty": v["stock_qty"],
                }
                for v in parsed_variants
            ]
            if variants_supplied
            else None,
            "images": [img["source_ref"] for img in parsed_images] if images_supplied else None,
        }
        payload_hash = hashlib.sha256(
            json.dumps(canonical_state, sort_keys=True).encode("utf-8")
        ).hexdigest()

        now_ts = timezone.now()
        raw_slug = self._extract_first(norm, ("slug", "product_slug"))
        ext_slug = str(raw_slug).strip() if raw_slug not in (None, "") else slugify(name_str)
        raw_source_url = self._extract_first(norm, ("source_url", "external_url"))
        source_url = str(raw_source_url).strip() if raw_source_url not in (None, "") else ""

        was_unchanged = bool(
            existing_product is not None
            and existing_source is not None
            and existing_source.raw_payload_hash == payload_hash
            and not existing_product.is_development_fixture
        )

        # 10. Create or Update Product
        is_created = existing_product is None
        if is_created:
            product = Product(
                category=category,
                brand=brand,
                name=name_str,
                sku=sku,
                sku_prefix=sku,
                currency=validated_currency,
                selling_price=effective_selling_price,
                compare_at_price=effective_compare_at_price,
                wholesale_cost_price=effective_supplier_cost,
                description=final_description,
                short_description=final_short_description,
                description_html=final_description_html,
                fabric_composition=fabric_comp_str,
                image_fit=image_fit_val,
                is_featured=is_featured_val,
                is_new_arrival=is_new_arrival_val,
                is_hot_deal=is_hot_deal_val,
                sort_order=sort_order_val,
                inside_dhaka_shipping_override=inside_dhaka_val,
                outside_dhaka_shipping_override=outside_dhaka_val,
                has_variants=bool(variants_supplied and len(parsed_variants) > 0),
                track_inventory=bool(
                    stock_supplied
                    or (variants_supplied and any(v["stock_supplied"] for v in parsed_variants))
                ),
                stock_qty=product_stock_qty if stock_supplied else 0,
                is_active=True,
                is_development_fixture=False,
            )
            product.full_clean()
            product.save()
        else:
            product = existing_product
            product.category = category
            product.brand = brand
            product.name = name_str
            product.sku = sku
            product.sku_prefix = sku
            product.currency = validated_currency
            product.selling_price = effective_selling_price
            product.compare_at_price = effective_compare_at_price
            product.wholesale_cost_price = effective_supplier_cost
            product.description = final_description
            product.short_description = final_short_description
            product.description_html = final_description_html
            product.fabric_composition = fabric_comp_str
            product.image_fit = image_fit_val
            product.is_featured = is_featured_val
            product.is_new_arrival = is_new_arrival_val
            product.is_hot_deal = is_hot_deal_val
            product.sort_order = sort_order_val
            product.inside_dhaka_shipping_override = inside_dhaka_val
            product.outside_dhaka_shipping_override = outside_dhaka_val
            product.is_development_fixture = False

            if variants_supplied:
                product.has_variants = len(parsed_variants) > 0
            elif self.options.overwrite_blank:
                product.has_variants = False

            if stock_supplied:
                product.track_inventory = True
                if not product.has_variants and not (
                    not variants_supplied and not self.options.overwrite_blank and product.variants.exists()
                ):
                    product.stock_qty = product_stock_qty
            elif variants_supplied and any(v["stock_supplied"] for v in parsed_variants):
                product.track_inventory = True
            elif self.options.overwrite_blank and not variants_supplied:
                product.track_inventory = False
                product.stock_qty = 0

            product.full_clean()
            product.save()

        # 11. Upsert Variants (only when source supplies variants, or clear if overwrite_blank=True)
        if variants_supplied:
            kept_variant_ids = []
            for v_spec in parsed_variants:
                existing_var = ProductVariant.objects.filter(
                    product=product, sku__iexact=v_spec["sku"]
                ).first()
                if existing_var:
                    existing_var.sku = v_spec["sku"]
                    if v_spec["size_label"] or self.options.overwrite_blank:
                        existing_var.size_label = v_spec["size_label"]
                    if v_spec["color_label"] or self.options.overwrite_blank:
                        existing_var.color_label = v_spec["color_label"]
                    if v_spec["option_name"] or self.options.overwrite_blank:
                        existing_var.option_name = v_spec["option_name"]
                    if v_spec["option_value"] or self.options.overwrite_blank:
                        existing_var.option_value = v_spec["option_value"]
                    existing_var.retail_price = v_spec["retail_price"]
                    existing_var.price_override = v_spec["price_override"]
                    existing_var.compare_at_price = v_spec["compare_at_price"]
                    if v_spec["wholesale_price"] is not None or self.options.overwrite_blank:
                        existing_var.wholesale_price = v_spec["wholesale_price"]
                    if v_spec["stock_supplied"]:
                        existing_var.stock_qty = max(existing_var.reserved_qty, v_spec["stock_qty"])
                    elif self.options.overwrite_blank:
                        existing_var.stock_qty = existing_var.reserved_qty
                    existing_var.is_active = True
                    existing_var.full_clean()
                    existing_var.save()
                    kept_variant_ids.append(existing_var.pk)
                else:
                    new_var = ProductVariant(
                        product=product,
                        sku=v_spec["sku"],
                        size_label=v_spec["size_label"],
                        color_label=v_spec["color_label"],
                        option_name=v_spec["option_name"],
                        option_value=v_spec["option_value"],
                        retail_price=v_spec["retail_price"],
                        price_override=v_spec["price_override"],
                        compare_at_price=v_spec["compare_at_price"],
                        wholesale_price=v_spec["wholesale_price"],
                        stock_qty=v_spec["stock_qty"] if v_spec["stock_supplied"] else 0,
                        is_active=True,
                    )
                    new_var.full_clean()
                    new_var.save()
                    kept_variant_ids.append(new_var.pk)

            if self.options.overwrite_blank:
                product.variants.exclude(pk__in=kept_variant_ids).delete()
            if kept_variant_ids:
                product.sync_stock_from_variants()
        elif self.options.overwrite_blank and not is_created:
            product.variants.all().delete()

        # 12. Upsert Images (only when source supplies images, or clear if overwrite_blank=True)
        if images_supplied and parsed_images:
            kept_image_ids = []
            for idx, img_spec in enumerate(parsed_images):
                existing_img = (
                    ProductImage.objects.filter(product=product, image_url=img_spec["source_ref"])
                    .exclude(pk__in=kept_image_ids)
                    .first()
                )
                if existing_img:
                    existing_img.alt_text = img_spec["alt_text"]
                    existing_img.display_order = idx
                    existing_img.sort_order = idx
                    existing_img.is_primary = idx == 0
                    if img_spec["downloaded_name"] and img_spec["downloaded_bytes"]:
                        existing_img.image.save(
                            img_spec["downloaded_name"],
                            ContentFile(img_spec["downloaded_bytes"]),
                            save=False,
                        )
                    elif img_spec["source_ref"].startswith("/media/"):
                        existing_img.image.name = urllib.parse.unquote(img_spec["source_ref"])[len("/media/") :]
                    existing_img.full_clean()
                    existing_img.save()
                    kept_image_ids.append(existing_img.pk)
                else:
                    img_obj = ProductImage(
                        product=product,
                        image_url=img_spec["source_ref"],
                        alt_text=img_spec["alt_text"],
                        display_order=idx,
                        sort_order=idx,
                        is_primary=(idx == 0),
                    )
                    if img_spec["downloaded_name"] and img_spec["downloaded_bytes"]:
                        img_obj.image.save(
                            img_spec["downloaded_name"],
                            ContentFile(img_spec["downloaded_bytes"]),
                            save=False,
                        )
                    elif img_spec["source_ref"].startswith("/media/"):
                        img_obj.image.name = urllib.parse.unquote(img_spec["source_ref"])[len("/media/") :]
                    img_obj.full_clean()
                    img_obj.save()
                    kept_image_ids.append(img_obj.pk)
            product.images.exclude(pk__in=kept_image_ids).delete()
        elif images_supplied and not parsed_images and self.options.overwrite_blank and not is_created:
            product.images.all().delete()
        elif not images_supplied and self.options.overwrite_blank and not is_created:
            product.images.all().delete()

        # 13. Upsert ProductSource and SupplierProductMap with timestamps & margin audit
        ProductSource.objects.update_or_create(
            source_name=source_name,
            external_id=ext_id,
            defaults={
                "product": product,
                "external_sku": sku,
                "external_slug": ext_slug,
                "source_url": source_url,
                "sync_status": ProductSource.SyncStatus.SYNCED,
                "last_synced_at": now_ts,
                "sync_error_message": "",
                "raw_payload_hash": payload_hash,
                "is_development_fixture": False,
                "raw_data": {
                    "external_id": ext_id,
                    "sku": sku,
                    "supplier_cost": str(effective_supplier_cost) if effective_supplier_cost is not None else None,
                    "selling_price": str(effective_selling_price),
                    "margin_percentage": str(product.margin_percentage) if product.margin_percentage is not None else None,
                    "stock_supplied": stock_supplied,
                    "variants_supplied": variants_supplied,
                    "images_supplied": images_supplied,
                },
            },
        )

        # Clean up any stale SAMPLE-* fixture ProductSource on this product
        product.sources.filter(source_name=source_name, is_development_fixture=True).exclude(
            external_id=ext_id
        ).delete()

        SupplierProductMap.objects.update_or_create(
            product=product,
            defaults={
                "source_name": source_name,
                "external_product_id": ext_id,
                "external_sku": sku,
                "external_slug": ext_slug,
                "supplier_cost": effective_supplier_cost,
                "supplier_suggested_price": supplier_suggested_price,
                "applied_margin_percent": product.margin_percentage,
                "raw_payload_hash": payload_hash,
                "last_synced_at": now_ts,
            },
        )

        if is_created:
            return "created", warnings
        if was_unchanged:
            return "unchanged", warnings
        return "updated", warnings

    def _resolve_product_pricing(
        self,
        supplier_cost: Decimal | None,
        selling_price: Decimal | None,
        compare_at_price: Decimal | None,
        row_margin_pct: Decimal | None,
        row_markup_pct: Decimal | None,
        existing_product: Product | None,
    ) -> tuple[Decimal | None, Decimal, Decimal | None]:
        """
        Enforces strict separation between supplier cost, customer selling price, and margin.
        Never assumes supplier cost is the customer retail selling price.
        """
        # 1. Effective supplier cost
        if supplier_cost is not None:
            effective_supplier_cost = supplier_cost
        elif existing_product is not None and not self.options.overwrite_blank:
            effective_supplier_cost = existing_product.wholesale_cost_price
        else:
            effective_supplier_cost = None

        # 2. Validate margin/markup percentages if given
        margin_pct = (
            row_margin_pct
            if row_margin_pct is not None
            else self.options.default_margin_percent
        )
        markup_pct = (
            row_markup_pct
            if row_markup_pct is not None
            else self.options.default_markup_percent
        )

        if margin_pct is not None:
            if margin_pct >= Decimal("100.00") or (
                margin_pct < Decimal("0.00") and not self.options.allow_negative_margin
            ):
                raise ImportValidationError(
                    "margin_percent",
                    f"margin_percent ({margin_pct}%) must be between 0 and 99.99.",
                )
        if markup_pct is not None:
            if markup_pct < Decimal("0.00") and not self.options.allow_negative_margin:
                raise ImportValidationError(
                    "markup_percent",
                    f"markup_percent ({markup_pct}%) cannot be negative.",
                )

        # 3. Effective customer selling price
        if selling_price is not None:
            effective_selling_price = selling_price
        elif (
            effective_supplier_cost is not None
            and effective_supplier_cost > Decimal("0.00")
            and (margin_pct is not None or markup_pct is not None)
        ):
            if margin_pct is not None:
                divisor = Decimal("1.00") - (margin_pct / Decimal("100.00"))
                effective_selling_price = (effective_supplier_cost / divisor).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                multiplier = Decimal("1.00") + (markup_pct / Decimal("100.00"))
                effective_selling_price = (effective_supplier_cost * multiplier).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
        elif existing_product is not None and not self.options.overwrite_blank:
            effective_selling_price = existing_product.selling_price
        else:
            if effective_supplier_cost is not None:
                raise ImportValidationError(
                    "selling_price",
                    "Missing customer selling_price: supplier_cost is never assumed to be your retail selling price. "
                    "Supply an explicit selling_price or specify margin_percent / markup_percent.",
                )
            raise ImportValidationError(
                "selling_price",
                "Missing required field 'selling_price' (or supplier_cost with margin_percent / markup_percent).",
            )

        # 4. Guard against selling below supplier cost (negative margin)
        if (
            effective_supplier_cost is not None
            and effective_selling_price < effective_supplier_cost
            and not self.options.allow_negative_margin
        ):
            raise ImportValidationError(
                "selling_price",
                f"Customer selling_price ({effective_selling_price}) cannot be less than supplier_cost ({effective_supplier_cost}).",
            )

        # 5. Effective compare_at_price
        if compare_at_price is not None:
            if compare_at_price == Decimal("0.00") or compare_at_price == effective_selling_price:
                effective_compare_at = None
            elif compare_at_price < effective_selling_price:
                raise ImportValidationError(
                    "compare_at_price",
                    f"compare_at_price ({compare_at_price}) must be greater than or equal to selling_price ({effective_selling_price}).",
                )
            else:
                effective_compare_at = compare_at_price
        elif existing_product is not None and not self.options.overwrite_blank:
            if (
                existing_product.compare_at_price is not None
                and existing_product.compare_at_price > effective_selling_price
            ):
                effective_compare_at = existing_product.compare_at_price
            else:
                effective_compare_at = None
        else:
            effective_compare_at = None

        return effective_supplier_cost, effective_selling_price, effective_compare_at

    def _parse_variants(
        self,
        norm: dict[str, Any],
        product_sku: str,
        product_selling_price: Decimal,
        product_compare_at_price: Decimal | None,
        product_supplier_cost: Decimal | None,
        row_currency: Any,
        existing_product: Product | None,
        seen_product_skus: dict[str, int],
        seen_variant_skus: dict[str, int],
        row_num: int,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """
        Extracts variants ONLY when the source actually supplies them.
        Supports:
        1. `variants` / `variants_json` (JSON list or semicolon-separated `SKU:Size:Color:Stock:Price` specs)
        2. Single-variant columns (`variant_sku`, `variant_size`, `variant_color`, `variant_option_name`, `variant_option_value`)
        3. Droploo variable product payloads (`is_variable == 1` with variant objects inside `product_images`)
        """
        raw_variants = self._extract_first(norm, ("variants", "variants_json"))
        raw_variant_items: list[dict[str, Any]] = []
        variants_supplied = False

        if raw_variants is not None and str(raw_variants).strip() != "":
            variants_supplied = True
            if isinstance(raw_variants, list):
                for item in raw_variants:
                    if isinstance(item, dict):
                        raw_variant_items.append(self._normalize_keys(item))
                    else:
                        raise ImportValidationError("variants", f"Invalid variant entry '{item}'.")
            elif isinstance(raw_variants, str):
                text = raw_variants.strip()
                if text.startswith("["):
                    try:
                        parsed_json = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise ImportValidationError("variants", f"Malformed JSON in variants column: {exc}") from exc
                    if not isinstance(parsed_json, list):
                        raise ImportValidationError("variants", "Variants JSON must be a list of objects.")
                    for item in parsed_json:
                        if not isinstance(item, dict):
                            raise ImportValidationError("variants", "Each variant in JSON must be an object.")
                        raw_variant_items.append(self._normalize_keys(item))
                else:
                    # Compact semicolon-delimited format: SKU:Size:Color:Stock[:SellingPrice[:SupplierCost]]
                    for part in text.split(";"):
                        part = part.strip()
                        if not part:
                            continue
                        tokens = [t.strip() for t in part.split(":")]
                        if len(tokens) < 2:
                            raise ImportValidationError(
                                "variants",
                                f"Invalid compact variant spec '{part}'. Expected 'SKU:Size[:Color[:Stock[:SellingPrice]]]'.",
                            )
                        v_dict: dict[str, Any] = {
                            "sku": tokens[0],
                            "size": tokens[1] if len(tokens) > 1 else "",
                            "color": tokens[2] if len(tokens) > 2 else "",
                        }
                        if len(tokens) > 3 and tokens[3] != "":
                            v_dict["stock"] = tokens[3]
                        if len(tokens) > 4 and tokens[4] != "":
                            v_dict["selling_price"] = tokens[4]
                        if len(tokens) > 5 and tokens[5] != "":
                            v_dict["supplier_cost"] = tokens[5]
                        raw_variant_items.append(v_dict)

        # Check single-variant inline columns if `variants` list was not provided
        elif any(
            self._extract_first(norm, (col,)) is not None
            for col in ("variant_sku", "variant_size", "variant_color", "variant_option_value")
        ):
            variants_supplied = True
            raw_variant_items.append(
                {
                    "sku": self._extract_first(norm, ("variant_sku",)) or f"{product_sku}-V1",
                    "size": self._extract_first(norm, ("variant_size",)) or "",
                    "color": self._extract_first(norm, ("variant_color",)) or "",
                    "option_name": self._extract_first(norm, ("variant_option_name",)) or "",
                    "option_value": self._extract_first(norm, ("variant_option_value",)) or "",
                    "selling_price": self._extract_first(norm, ("variant_price", "variant_selling_price")),
                    "compare_at_price": self._extract_first(norm, ("variant_compare_at_price",)),
                    "supplier_cost": self._extract_first(norm, ("variant_supplier_cost", "variant_wholesale_price")),
                    "stock": self._extract_first(norm, ("variant_stock",)),
                }
            )

        # Check Droploo `is_variable == 1` with `product_images` carrying size/color
        elif str(norm.get("is_variable", "")).strip() in ("1", "true", "True") and isinstance(
            norm.get("product_images"), list
        ):
            droploo_vars = []
            for idx, pimg in enumerate(norm["product_images"], start=1):
                if not isinstance(pimg, dict):
                    continue
                v_size = str(pimg.get("size") or "").strip()
                v_color = str(pimg.get("color") or "").strip()
                if not v_size and not v_color:
                    continue
                suffix = slugify(f"{v_size}-{v_color}").upper().replace("-", "") or f"V{idx}"
                droploo_vars.append(
                    {
                        "sku": f"{product_sku}-{suffix}",
                        "size": v_size,
                        "color": v_color,
                        "selling_price": pimg.get("selling_price") or pimg.get("retail_price"),
                        "supplier_cost": pimg.get("supplier_cost") or pimg.get("wholesale_price"),
                        "stock": pimg.get("qty") if pimg.get("qty") is not None else pimg.get("stock"),
                    }
                )
            if droploo_vars:
                variants_supplied = True
                raw_variant_items.extend(droploo_vars)

        if not variants_supplied:
            return False, []

        parsed_variants: list[dict[str, Any]] = []
        local_skus: set[str] = set()

        for idx, v_raw in enumerate(raw_variant_items, start=1):
            v_sku = str(self._extract_first(v_raw, ("sku", "variant_sku")) or "").strip()
            if not v_sku:
                raise ImportValidationError("variants", f"Variant #{idx} is missing a 'sku'.")
            v_sku_upper = v_sku.upper()

            if v_sku_upper in local_skus:
                raise ImportValidationError(
                    "variants",
                    f"Duplicate variant SKU '{v_sku}' within the same product row.",
                    is_duplicate=True,
                )
            if v_sku_upper in seen_variant_skus:
                first_r = seen_variant_skus[v_sku_upper]
                raise ImportValidationError(
                    "variants",
                    f"Duplicate variant SKU '{v_sku}' (already used on row {first_r}).",
                    is_duplicate=True,
                )
            if v_sku_upper in seen_product_skus and v_sku_upper != product_sku.upper():
                first_r = seen_product_skus[v_sku_upper]
                raise ImportValidationError(
                    "variants",
                    f"Variant SKU '{v_sku}' conflicts with product SKU on row {first_r}.",
                    is_duplicate=True,
                )

            # Check DB for variant SKU conflicts on other products
            conflict_var_qs = ProductVariant.objects.filter(sku__iexact=v_sku)
            if existing_product:
                conflict_var_qs = conflict_var_qs.exclude(product=existing_product)
            if conflict_var_qs.exists():
                raise ImportValidationError(
                    "variants",
                    f"Duplicate variant SKU conflict: '{v_sku}' is already assigned to another product's variant.",
                    is_duplicate=True,
                )

            local_skus.add(v_sku_upper)

            v_selling = parse_price_and_currency(
                self._extract_first(v_raw, ("selling_price", "retail_price", "price_override", "price")),
                field_name="variant_selling_price",
                row_currency_raw=row_currency,
                expected_currency=self.store_currency,
                allow_zero=False,
            )
            v_compare = parse_price_and_currency(
                self._extract_first(v_raw, ("compare_at_price", "original_price")),
                field_name="variant_compare_at_price",
                row_currency_raw=row_currency,
                expected_currency=self.store_currency,
                allow_zero=True,
            )
            v_cost = parse_price_and_currency(
                self._extract_first(v_raw, ("supplier_cost", "wholesale_price", "cost_price")),
                field_name="variant_supplier_cost",
                row_currency_raw=row_currency,
                expected_currency=self.store_currency,
                allow_zero=True,
            )

            eff_v_cost = v_cost if v_cost is not None else product_supplier_cost
            eff_v_price = v_selling if v_selling is not None else product_selling_price

            if (
                eff_v_cost is not None
                and eff_v_price < eff_v_cost
                and not self.options.allow_negative_margin
            ):
                raise ImportValidationError(
                    "variants",
                    f"Variant '{v_sku}' selling price ({eff_v_price}) is lower than supplier cost ({eff_v_cost}).",
                )

            if v_compare is not None:
                if v_compare == Decimal("0.00") or v_compare == eff_v_price:
                    eff_v_compare = None
                elif v_compare < eff_v_price:
                    raise ImportValidationError(
                        "variants",
                        f"Variant '{v_sku}' compare_at_price ({v_compare}) cannot be less than selling price ({eff_v_price}).",
                    )
                else:
                    eff_v_compare = v_compare
            else:
                eff_v_compare = (
                    product_compare_at_price
                    if product_compare_at_price and product_compare_at_price > eff_v_price
                    else None
                )

            v_stock_supplied, v_stock_qty = parse_optional_stock(
                self._extract_first(v_raw, ("stock", "stock_qty", "stock_quantity", "qty")),
                field_name="variant_stock",
            )

            parsed_variants.append(
                {
                    "sku": v_sku,
                    "size_label": str(self._extract_first(v_raw, ("size", "size_label")) or "").strip(),
                    "color_label": str(self._extract_first(v_raw, ("color", "color_label")) or "").strip(),
                    "option_name": str(self._extract_first(v_raw, ("option_name",)) or "").strip(),
                    "option_value": str(self._extract_first(v_raw, ("option_value",)) or "").strip(),
                    "price_override": v_selling,
                    "retail_price": eff_v_price,
                    "compare_at_price": eff_v_compare,
                    "wholesale_price": eff_v_cost,
                    "stock_supplied": v_stock_supplied,
                    "stock_qty": v_stock_qty,
                }
            )

        return True, parsed_variants

    def _parse_and_process_images(
        self,
        norm: dict[str, Any],
        product_sku: str,
        product_name: str,
        row_num: int,
        ext_id: str,
    ) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Extracts and validates images ONLY when the source actually supplies them.
        Unavailable or invalid images are recorded as non-fatal row warnings and safely skipped.
        """
        image_keys = ("images", "image_urls", "image_url", "primary_image", "gallery_images", "imageurl")
        raw_images_list: list[str] = []
        images_supplied = False

        for key in image_keys:
            val = norm.get(key)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                continue
            images_supplied = True
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        u = item.get("url") or item.get("image_url") or item.get("imageUrl")
                        if u:
                            raw_images_list.append(str(u).strip())
                    elif item:
                        raw_images_list.append(str(item).strip())
            elif isinstance(val, str):
                text = val.strip()
                if text.startswith("["):
                    try:
                        arr = json.loads(text)
                        if isinstance(arr, list):
                            for item in arr:
                                if item:
                                    raw_images_list.append(str(item).strip())
                            continue
                    except json.JSONDecodeError:
                        pass
                # Split by pipe or semicolon or comma (if comma is followed by http or /)
                if "|" in text:
                    parts = text.split("|")
                elif ";" in text:
                    parts = text.split(";")
                else:
                    parts = re.split(r",\s*(?=https?://|/)", text)
                for p in parts:
                    if p.strip():
                        raw_images_list.append(p.strip())

        # Also check Droploo `product_images` gallery array if present
        if isinstance(norm.get("product_images"), list):
            for pimg in norm["product_images"]:
                if isinstance(pimg, dict) and pimg.get("imageUrl"):
                    images_supplied = True
                    raw_images_list.append(str(pimg["imageUrl"]).strip())

        if not images_supplied:
            return False, [], []

        # Deduplicate while preserving order
        seen_refs: set[str] = set()
        valid_images: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        for idx, raw_ref in enumerate(raw_images_list, start=1):
            norm_ref = SafeImageHandler.normalize_image_reference(raw_ref)
            if not norm_ref:
                warnings.append(
                    {
                        "row": row_num,
                        "external_id": ext_id,
                        "sku": product_sku,
                        "field": "images",
                        "warning": f"Skipped invalid or unsupported image URL '{raw_ref}'.",
                    }
                )
                continue

            if norm_ref in seen_refs:
                continue
            seen_refs.add(norm_ref)

            downloaded_name = None
            downloaded_bytes = None

            if self.options.download_images and norm_ref.startswith(("http://", "https://")):
                dl_name, dl_bytes, dl_err = SafeImageHandler.download_authorized_image(
                    url=norm_ref,
                    product_sku=product_sku,
                )
                if dl_err:
                    warnings.append(
                        {
                            "row": row_num,
                            "external_id": ext_id,
                            "sku": product_sku,
                            "field": "images",
                            "warning": dl_err,
                        }
                    )
                    # Do not attach unavailable external images when download fails
                    continue
                downloaded_name = dl_name
                downloaded_bytes = dl_bytes

            valid_images.append(
                {
                    "source_ref": norm_ref,
                    "alt_text": f"{product_name} (Image {len(valid_images) + 1})",
                    "downloaded_name": downloaded_name,
                    "downloaded_bytes": downloaded_bytes,
                }
            )

        return True, valid_images, warnings
