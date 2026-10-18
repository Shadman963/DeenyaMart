"""Core models for DeenyaMart: SiteSettings, ContentPage, NewsletterSubscriber."""
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base class with created_at and updated_at timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SiteSettings(TimeStampedModel):
    """Singleton configuration for storefront identity, announcement bar, hero, and policies."""

    site_name = models.CharField(max_length=80, default="DeenyaMart")
    tagline = models.CharField(
        max_length=200,
        default="Thoughtful Modest Wear & Everyday Essentials, Delivered Across Bangladesh.",
    )
    currency_symbol = models.CharField(max_length=8, default="৳")
    currency_code = models.CharField(max_length=8, default="BDT")

    # 1. Slim announcement bar configuration
    announcement_enabled = models.BooleanField(default=True)
    announcement_badge = models.CharField(max_length=40, default="Nationwide COD", blank=True)
    announcement_text = models.CharField(
        max_length=240,
        default="Cash on Delivery across Bangladesh · Inside Dhaka ৳60 (1–2 Days) · Outside Dhaka ৳120",
    )
    announcement_cta_text = models.CharField(max_length=60, default="", blank=True)
    announcement_cta_url = models.CharField(max_length=200, default="", blank=True)

    # 4. Editorial Hero configuration
    hero_eyebrow = models.CharField(
        max_length=90,
        default="Seasonal Archive · Natural Linen, Breathable Cotton & Cherry Weaves",
    )
    hero_headline = models.CharField(
        max_length=160,
        default="Refined Modest Wear & Mindful Home Essentials.",
    )
    hero_subheadline = models.TextField(
        default=(
            "Tailored Khimars, Salat Hijabs, artisanal Panjabis, and curated household tools—"
            "crafted in breathable fabrics for everyday comfort across Bangladesh."
        ),
    )
    hero_primary_cta_text = models.CharField(max_length=50, default="Shop Now")
    hero_primary_cta_url = models.CharField(max_length=200, default="/shop/")
    hero_secondary_cta_text = models.CharField(max_length=60, default="Browse Categories")
    hero_secondary_cta_url = models.CharField(max_length=200, default="#categories-section")

    # 7. Promotional Collection Banner configuration
    promo_banner_eyebrow = models.CharField(
        max_length=80,
        default="Curated Textile Story · Breathable Weaves",
    )
    promo_banner_title = models.CharField(
        max_length=160,
        default="The Linen & Pure Cotton Modest Edit",
    )
    promo_banner_subtitle = models.TextField(
        default=(
            "Lightweight, opaque silhouettes designed for effortless prayer and daily wear. "
            "Featuring full-coverage Khimars, Kids' matching sets, and fine-stitched Panjabis."
        ),
    )
    promo_banner_cta_text = models.CharField(max_length=60, default="Explore the Collection")
    promo_banner_cta_url = models.CharField(max_length=200, default="/category/womens-modest-wear/")
    promo_banner_secondary_text = models.CharField(max_length=60, default="View Kids' Sets", blank=True)
    promo_banner_secondary_url = models.CharField(max_length=200, default="/category/kids-modest-wear/", blank=True)

    # 9. Verified Trust & Fulfillment facts
    cod_enabled = models.BooleanField(default=True)
    cod_verification_note = models.CharField(
        max_length=180,
        default="Pay in cash at your doorstep upon receiving your parcel. Orders are confirmed via phone/SMS.",
    )
    return_window_days = models.PositiveSmallIntegerField(default=7)
    return_policy_summary = models.CharField(
        max_length=200,
        default="Inspect your parcel at delivery. Easy 7-day size exchange or return for unused items in original packaging.",
    )

    # 11. Contact & Social configuration (social links only rendered when non-empty)
    support_phone = models.CharField(max_length=32, default="+880 1700-000000")
    support_hours = models.CharField(max_length=80, default="Saturday – Thursday, 10:00 AM – 8:00 PM BST")
    support_email = models.EmailField(default="care@deenyamart.com")
    headquarters_address = models.CharField(
        max_length=220,
        default="House 14, Road 7, Sector 4, Uttara, Dhaka-1230, Bangladesh",
    )
    facebook_url = models.URLField(blank=True, default="https://facebook.com/deenyamart")
    instagram_url = models.URLField(blank=True, default="https://instagram.com/deenyamart")
    youtube_url = models.URLField(blank=True, default="")
    whatsapp_number = models.CharField(max_length=32, blank=True, default="+8801700000000")

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self) -> str:
        return self.site_name

    @classmethod
    def get_solo(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ContentPage(TimeStampedModel):
    """Static/editorial policy and informational pages (Return Policy, Privacy, Terms, About)."""

    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True)
    summary = models.CharField(max_length=280, blank=True)
    body_html = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class NewsletterSubscriber(TimeStampedModel):
    """Server-side stored newsletter subscription records."""

    email = models.EmailField(unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=40, default="homepage_footer")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.email
