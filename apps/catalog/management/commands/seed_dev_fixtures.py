"""
Management command to seed clearly identifiable development fixtures for DeenyaMart.
Uses normalized catalog categories and verified sample SKUs from docs/implementation-plan.md.
"""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import (
    Brand,
    Category,
    Product,
    ProductImage,
    ProductSource,
    ProductVariant,
)
from apps.core.models import ContentPage, SiteSettings
from apps.shipping.models import ShippingZone



class Command(BaseCommand):
    help = "Seeds DeenyaMart development fixtures (categories, shipping zones, policies, and sample products)."

    @transaction.atomic
    def handle(self, *args, **options):
        # 1. Site Settings
        SiteSettings.get_solo()

        # 2. Verified Bangladesh Shipping Zones
        zones_data = [
            {
                "code": "INSIDE_DHAKA",
                "name": "Inside Dhaka City",
                "coverage_summary": "Uttara, Dhanmondi, Mirpur, Gulshan, Banani, Mohammadpur & Metro Thanas",
                "base_rate": Decimal("60.00"),
                "estimated_delivery_days": "1–2 Business Days",
                "sort_order": 1,
            },
            {
                "code": "SUB_DHAKA",
                "name": "Dhaka Sub-Area",
                "coverage_summary": "Savar, Ashulia, Keraniganj, Gazipur Sadar, Narayanganj",
                "base_rate": Decimal("100.00"),
                "estimated_delivery_days": "2–3 Business Days",
                "sort_order": 2,
            },
            {
                "code": "OUTSIDE_DHAKA",
                "name": "Outside Dhaka (Nationwide)",
                "coverage_summary": "All 64 Districts across Bangladesh via Steadfast / Pathao Courier",
                "base_rate": Decimal("120.00"),
                "estimated_delivery_days": "3–5 Business Days",
                "sort_order": 3,
            },
        ]
        for z in zones_data:
            ShippingZone.objects.update_or_create(code=z["code"], defaults=z)

        # 3. Real Normalized Catalog Categories & Subcategories
        categories_spec = [
            {
                "name": "Women's Modest Wear",
                "slug": "womens-modest-wear",
                "subtitle": "Khimars, Salat Hijabs, Jilbabs & Everyday Modest Sets",
                "description": "Full-coverage modest silhouettes tailored in breathable linen, combed cotton, and wrinkle-resistant cherry weaves.",
                "image_url": "/static/img/categories/cat-womens-modest.png",
                "accent_tone": "emerald",
                "is_featured": True,
                "sort_order": 1,
                "children": [
                    ("Woman Khimar", "woman-khimar", "Full-coverage two-piece and layered khimar sets"),
                    ("Salat Hijab", "salat-hijab", "One-piece prayer hijabs with soft chin coverage"),
                    ("Woman Jilbab", "woman-jilbab", "Flowing ankle-length jilbab ensembles"),
                    ("Woman Hijab", "woman-hijab", "Everyday breathable shawls and hijabs"),
                    ("Woman Borka", "woman-borka", "Modest outer abayas and borkas"),
                ],
            },
            {
                "name": "Men's Modest Wear",
                "slug": "mens-modest-wear",
                "subtitle": "Tailored Panjabis, Moroccan Jubbas & Kabli Sets",
                "description": "Clean-cut traditional and contemporary modest attire crafted for Friday prayers, gatherings, and daily comfort.",
                "image_url": "/static/img/categories/cat-mens-modest.png",
                "accent_tone": "brass",
                "is_featured": True,
                "sort_order": 2,
                "children": [
                    ("Panjabi", "panjabi", "Pure cotton and textured linen panjabis"),
                    ("Jubba", "jubba", "Ankle-length thobes and Moroccan jubbas"),
                    ("Kabli", "kabli", "Two-piece coordinated kabli suits"),
                ],
            },
            {
                "name": "Kids' Modest Wear",
                "slug": "kids-modest-wear",
                "subtitle": "Gentle Cotton & Linen Khimar Sets for Ages 3–12",
                "description": "Lightweight, non-restrictive modest wear sets sized specifically for children's comfort in warm weather.",
                "image_url": "/static/img/categories/cat-kids-modest.png",
                "accent_tone": "terracotta",
                "is_featured": True,
                "sort_order": 3,
                "children": [
                    ("Kids Khimar", "kids-khimar", "Two-piece khimar and skirt sets for girls"),
                    ("Kids Hijab", "kids-hijab", "Easy pull-on hijabs for school and madrasa"),
                    ("Kids Borka", "kids-borka", "Lightweight modest sets for juniors"),
                ],
            },
            {
                "name": "Home & Kitchen",
                "slug": "home-kitchen",
                "subtitle": "Countertop Organizers, Spice Racks & Utility Tools",
                "description": "Durable, thoughtfully designed kitchen organization and household hardware built for daily family use.",
                "image_url": "/static/img/categories/cat-home-kitchen.png",
                "accent_tone": "emerald",
                "is_featured": True,
                "sort_order": 4,
                "children": [
                    ("Kitchen Organizers", "kitchen-organizers", "Countertop racks, jar sets, and storage"),
                    ("Home Tools", "home-tools", "Practical household maintenance essentials"),
                ],
            },
            {
                "name": "Smart Gadgets",
                "slug": "smart-gadgets",
                "subtitle": "Wireless Neckbands, Smart Wearables & Daily Tech",
                "description": "Verified everyday audio accessories and smart utility devices.",
                "image_url": "/static/img/categories/cat-smart-gadgets.png",
                "accent_tone": "sand",
                "is_featured": True,
                "sort_order": 5,
                "children": [
                    ("Wireless Audio", "wireless-audio", "Bluetooth neckbands and earphones"),
                    ("Smart Wearables", "smart-wearables", "Fitness and call-enabled smartwatches"),
                ],
            },
            {
                "name": "Home Decor & Living",
                "slug": "home-decor",
                "subtitle": "Architectural Metal Lanterns & Warm Living Accents",
                "description": "Powder-coated metal accents and warm interior objects inspired by heritage geometry.",
                "image_url": "/static/img/categories/cat-home-decor.png",
                "accent_tone": "brass",
                "is_featured": True,
                "sort_order": 6,
                "children": [
                    ("Metal Items", "metal-items", "Geometric lanterns, stands, and metalwork"),
                ],
            },
        ]

        cat_map = {}
        for cdata in categories_spec:
            children = cdata.pop("children", [])
            parent_cat, _ = Category.objects.update_or_create(
                slug=cdata["slug"],
                defaults={**cdata, "parent": None, "is_active": True},
            )
            cat_map[parent_cat.slug] = parent_cat
            for idx, (sub_name, sub_slug, sub_title) in enumerate(children, start=1):
                sub_cat, _ = Category.objects.update_or_create(
                    slug=sub_slug,
                    defaults={
                        "parent": parent_cat,
                        "name": sub_name,
                        "subtitle": sub_title,
                        "is_active": True,
                        "sort_order": idx,
                    },
                )
                cat_map[sub_slug] = sub_cat

        # 4. Brands / Fabric Groupings
        brands_spec = [
            ("Natural Linen", "natural-linen", "180 GSM breathable textured linen weave", True),
            ("Pure Cotton", "pure-cotton", "Soft combed long-staple cotton", True),
            ("Cherry Crepe", "cherry-crepe", "Opaque wrinkle-resistant flowing crepe", True),
            ("Studio Utility", "studio-utility", "Tested electronics and household hardware", False),
        ]
        brand_map = {}
        for b_name, b_slug, b_note, b_feat in brands_spec:
            brand, _ = Brand.objects.update_or_create(
                slug=b_slug,
                defaults={
                    "name": b_name,
                    "fabric_note": b_note,
                    "is_featured": b_feat,
                    "is_active": True,
                },
            )
            brand_map[b_slug] = brand

        # 5. Development Fixture Products (clearly marked is_development_fixture=True)
        # Note: rating_count=0 and total_sales=0 so zero fake ratings or fake sales claims appear.
        products_spec = [
            {
                "sku_prefix": "LF104",
                "name": "Lilen Fabric Khimar Set LF104",
                "slug": "lilen-fabric-khimar-set-lf104",
                "category": cat_map["woman-khimar"],
                "brand": brand_map["natural-linen"],
                "base_regular_price": Decimal("1350.00"),
                "base_sale_price": None,
                "fabric_composition": "100% Breathable Textured Linen",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 1,
                "short_description": "Two-piece full-coverage linen khimar and matching flared skirt with adjustable tie-back headband.",
                "images": [
                    ("/static/img/fixtures/lf104-primary.svg", "Lilen Fabric Khimar Set LF104 front drape view", True),
                    ("/static/img/fixtures/lf104-secondary.svg", "Lilen Fabric Khimar Set LF104 back silhouette and tie detail", False),
                ],
                "variants": [
                    ("LF104-FREE-SAGE", "Free Size (Long 54\")", "Deep Cypress", 12),
                    ("LF104-FREE-SAND", "Free Size (Long 54\")", "Warm Sand", 6),
                ],
            },
            {
                "sku_prefix": "SH103",
                "name": "Cotton Semi Long Salat Hijab SH103",
                "slug": "cotton-semi-long-salat-hijab-sh103",
                "category": cat_map["salat-hijab"],
                "brand": brand_map["pure-cotton"],
                "base_regular_price": Decimal("890.00"),
                "base_sale_price": Decimal("790.00"),
                "fabric_composition": "100% Combed Pure Cotton",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 2,
                "short_description": "Slip-on semi-long prayer hijab with soft stretch chin panel for instant opaque coverage.",
                "images": [
                    ("/static/img/fixtures/sh103-primary.svg", "Cotton Semi Long Salat Hijab SH103 front view", True),
                    ("/static/img/fixtures/sh103-secondary.svg", "Cotton Semi Long Salat Hijab SH103 chin and hem detail", False),
                ],
                "variants": [
                    ("SH103-FREE-OLV", "Free Size (46\")", "Olive Ink", 16),
                    ("SH103-FREE-IVR", "Free Size (46\")", "Alabaster Ivory", 8),
                ],
            },
            {
                "sku_prefix": "KCK114",
                "name": "Kids Lilen Khimar Set KCK114",
                "slug": "kids-lilen-khimar-set-kck114",
                "category": cat_map["kids-khimar"],
                "brand": brand_map["natural-linen"],
                "base_regular_price": Decimal("750.00"),
                "base_sale_price": Decimal("650.00"),
                "fabric_composition": "Soft-Washed Junior Linen",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 3,
                "short_description": "Lightweight two-piece kids' khimar and elastic-waist skirt designed for ages 5 to 10.",
                "images": [
                    ("/static/img/fixtures/kck114-primary.svg", "Kids Lilen Khimar Set KCK114 front view", True),
                    ("/static/img/fixtures/kck114-secondary.svg", "Kids Lilen Khimar Set KCK114 two-piece layout", False),
                ],
                "variants": [
                    ("KCK114-AGE56", "Age 5–6", "Terracotta Rose", 5),
                    ("KCK114-AGE78", "Age 7–8", "Terracotta Rose", 6),
                    ("KCK114-AGE910", "Age 9–10", "Terracotta Rose", 3),
                ],
            },
            {
                "sku_prefix": "VCP108",
                "name": "Embroidered Cotton Panjabi VCP108",
                "slug": "embroidered-cotton-panjabi-vcp108",
                "category": cat_map["panjabi"],
                "brand": brand_map["pure-cotton"],
                "base_regular_price": Decimal("1450.00"),
                "base_sale_price": Decimal("1250.00"),
                "fabric_composition": "100% Two-Ply Combed Cotton",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 4,
                "short_description": "Relaxed-fit men's panjabi featuring subtle tone-on-tone geometric collar embroidery and side pockets.",
                "images": [
                    ("/static/img/fixtures/vcp108-primary.svg", "Embroidered Cotton Panjabi VCP108 front view", True),
                    ("/static/img/fixtures/vcp108-secondary.svg", "Embroidered Cotton Panjabi VCP108 placket stitch detail", False),
                ],
                "variants": [
                    ("VCP108-M40", "M (40)", "Deep Slate", 6),
                    ("VCP108-L42", "L (42)", "Deep Slate", 5),
                    ("VCP108-XL44", "XL (44)", "Deep Slate", 4),
                ],
            },
            {
                "sku_prefix": "KCK110",
                "name": "Kids Cotton Khimar Set KCK110",
                "slug": "kids-cotton-khimar-set-kck110",
                "category": cat_map["kids-khimar"],
                "brand": brand_map["pure-cotton"],
                "base_regular_price": Decimal("590.00"),
                "base_sale_price": None,
                "fabric_composition": "100% Breathable Jersey Cotton",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": False,
                "sort_order": 5,
                "short_description": "Everyday pull-on cotton khimar set for young girls with gentle stretch cuffs.",
                "images": [
                    ("/static/img/fixtures/kck110-primary.svg", "Kids Cotton Khimar Set KCK110 front view", True),
                    ("/static/img/fixtures/kck110-secondary.svg", "Kids Cotton Khimar Set KCK110 hem detail", False),
                ],
                "variants": [
                    ("KCK110-AGE56", "Age 5–6", "Forest Mint", 4),
                ],
            },
            {
                "sku_prefix": "WJB204",
                "name": "Full-Coverage Cherry Jilbab Set WJB204",
                "slug": "full-coverage-cherry-jilbab-set-wjb204",
                "category": cat_map["woman-jilbab"],
                "brand": brand_map["cherry-crepe"],
                "base_regular_price": Decimal("1690.00"),
                "base_sale_price": Decimal("1490.00"),
                "fabric_composition": "Premium Matte Cherry Crepe",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 6,
                "short_description": "Wrinkle-resistant travel and prayer jilbab set with elastic wrist cuffs and concealed side pockets.",
                "images": [
                    ("/static/img/fixtures/wjb204-primary.svg", "Full-Coverage Cherry Jilbab Set WJB204 front view", True),
                    ("/static/img/fixtures/wjb204-secondary.svg", "Full-Coverage Cherry Jilbab Set WJB204 wrist cuff detail", False),
                ],
                "variants": [
                    ("WJB204-FREE-CHR", "Free Size (56\")", "Charcoal Espresso", 11),
                ],
            },
            {
                "sku_prefix": "P109",
                "name": "Tailored Moroccan Jubba Thobe P109",
                "slug": "tailored-moroccan-jubba-thobe-p109",
                "category": cat_map["jubba"],
                "brand": brand_map["natural-linen"],
                "base_regular_price": Decimal("1850.00"),
                "base_sale_price": None,
                "fabric_composition": "Linen-Cotton Blend",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 7,
                "short_description": "Structured ankle-length jubba with concealed chest placket and reinforced side seam pockets.",
                "images": [
                    ("/static/img/fixtures/p109-primary.svg", "Tailored Moroccan Jubba Thobe P109 front view", True),
                    ("/static/img/fixtures/p109-secondary.svg", "Tailored Moroccan Jubba Thobe P109 pocket detail", False),
                ],
                "variants": [
                    ("P109-L54", "Length 54\" (L)", "Warm Taupe", 5),
                    ("P109-XL56", "Length 56\" (XL)", "Warm Taupe", 4),
                ],
            },
            {
                "sku_prefix": "KCKS123",
                "name": "Kids Cherry Khimar Set KCKS123",
                "slug": "kids-cherry-khimar-set-kcks123",
                "category": cat_map["kids-khimar"],
                "brand": brand_map["cherry-crepe"],
                "base_regular_price": Decimal("1120.00"),
                "base_sale_price": Decimal("988.00"),
                "fabric_composition": "Soft Cherry Crepe",
                "image_fit": "cover",
                "is_new_arrival": True,
                "is_featured": True,
                "sort_order": 8,
                "short_description": "Festive kids' cherry khimar ensemble with pleated sleeve cuffs and matching full-length skirt.",
                "images": [
                    ("/static/img/fixtures/kcks123-primary.svg", "Kids Cherry Khimar Set KCKS123 front view", True),
                    ("/static/img/fixtures/kcks123-secondary.svg", "Kids Cherry Khimar Set KCKS123 sleeve detail", False),
                ],
                "variants": [
                    ("KCKS123-AGE78", "Age 7–8", "Mulberry Berry", 7),
                    ("KCKS123-AGE910", "Age 9–10", "Mulberry Berry", 5),
                ],
            },
            {
                "sku_prefix": "ES71",
                "name": "Hoco ES71 Platinum Wireless Neckband",
                "slug": "hoco-es71-platinum-wireless-neckband",
                "category": cat_map["wireless-audio"],
                "brand": brand_map["studio-utility"],
                "base_regular_price": Decimal("1250.00"),
                "base_sale_price": Decimal("1000.00"),
                "fabric_composition": "Silicone Collar · Bluetooth 5.3",
                "image_fit": "contain",
                "is_new_arrival": False,
                "is_featured": True,
                "sort_order": 9,
                "short_description": "Lightweight magnetic-earbud wireless neckband with Type-C charging for lectures, calls, and daily commute.",
                "images": [
                    ("/static/img/fixtures/es71-primary.svg", "Hoco ES71 Platinum Wireless Neckband studio view", True),
                    ("/static/img/fixtures/es71-secondary.svg", "Hoco ES71 inline controls and charging port", False),
                ],
                "variants": [
                    ("ES71-BLK", "Standard", "Matte Black", 3),
                ],
            },
            {
                "sku_prefix": "HSR301",
                "name": "Brass & Steel Counter Spice Rack HSR301",
                "slug": "brass-steel-counter-spice-rack-hsr301",
                "category": cat_map["kitchen-organizers"],
                "brand": brand_map["studio-utility"],
                "base_regular_price": Decimal("1150.00"),
                "base_sale_price": Decimal("980.00"),
                "fabric_composition": "Powder-Coated Steel & Brass Accents",
                "image_fit": "contain",
                "is_new_arrival": False,
                "is_featured": True,
                "sort_order": 10,
                "short_description": "Two-tier freestanding countertop organizer for spice jars, tea canisters, and cooking oils.",
                "images": [
                    ("/static/img/fixtures/hsr301-primary.svg", "Brass and Steel Counter Spice Rack front view", True),
                    ("/static/img/fixtures/hsr301-secondary.svg", "Brass and Steel Counter Spice Rack tray view", False),
                ],
                "variants": [
                    ("HSR301-2TIER", "2-Tier (32cm)", "Brushed Brass & Charcoal", 8),
                ],
            },
            {
                "sku_prefix": "HDM402",
                "name": "Geometric Arch Metal Lantern Holder HDM402",
                "slug": "geometric-arch-metal-lantern-holder-hdm402",
                "category": cat_map["metal-items"],
                "brand": brand_map["studio-utility"],
                "base_regular_price": Decimal("850.00"),
                "base_sale_price": None,
                "fabric_composition": "Hand-Welded Iron with Matte Finish",
                "image_fit": "contain",
                "is_new_arrival": False,
                "is_featured": True,
                "sort_order": 11,
                "short_description": "Minimalist architectural arch candle and LED lantern holder for bedside or prayer corner.",
                "images": [
                    ("/static/img/fixtures/hdm402-primary.svg", "Geometric Arch Metal Lantern Holder studio view", True),
                ],
                "variants": [
                    ("HDM402-STD", "Standard (24cm)", "Antique Bronze", 10),
                ],
            },
            {
                "sku_prefix": "W10",
                "name": "Series 10 Smart Fitness Call Watch W10",
                "slug": "series-10-smart-fitness-call-watch-w10",
                "category": cat_map["smart-wearables"],
                "brand": brand_map["studio-utility"],
                "base_regular_price": Decimal("1650.00"),
                "base_sale_price": None,
                "fabric_composition": "Zinc Alloy Case · Silicone Strap",
                "image_fit": "contain",
                "is_new_arrival": False,
                "is_featured": True,
                "sort_order": 12,
                "short_description": "Daily step counter, prayer reminder alarms, and Bluetooth call notifications.",
                "images": [
                    ("/static/img/fixtures/w10-primary.svg", "Series 10 Smart Fitness Call Watch W10 studio view", True),
                ],
                "variants": [
                    ("W10-BLK", "45mm Strap", "Obsidian Black", 0),
                ],
            },
        ]

        now_ts = timezone.now()
        for idx_prod, pdata in enumerate(products_spec, start=1):
            images = pdata.pop("images")
            variants = pdata.pop("variants")
            sale_price = pdata["base_sale_price"]
            reg_price = pdata["base_regular_price"]
            selling_price = sale_price if sale_price else reg_price
            compare_at_price = reg_price if sale_price else None

            product, _ = Product.objects.update_or_create(
                sku_prefix=pdata["sku_prefix"],
                defaults={
                    **pdata,
                    "sku": pdata["sku_prefix"],
                    "currency": "BDT",
                    "selling_price": selling_price,
                    "compare_at_price": compare_at_price,
                    "description": pdata.get("short_description", ""),
                    "track_inventory": True,
                    "is_active": True,
                    "is_development_fixture": True,
                    "has_variants": len(variants) > 1,
                },
            )

            product.images.all().delete()
            for idx, (img_url, alt_text, is_prim) in enumerate(images):
                ProductImage.objects.create(
                    product=product,
                    image_url=img_url,
                    alt_text=alt_text,
                    is_primary=is_prim,
                    display_order=idx,
                    sort_order=idx,
                )

            for v_sku, v_size, v_color, v_stock in variants:
                ProductVariant.objects.update_or_create(
                    sku=v_sku,
                    defaults={
                        "product": product,
                        "size_label": v_size,
                        "color_label": v_color,
                        "retail_price": selling_price,
                        "compare_at_price": compare_at_price,
                        "stock_qty": v_stock,
                        "reserved_qty": 0,
                        "is_active": True,
                    },
                )
            product.sync_stock_from_variants()

            ProductSource.objects.update_or_create(
                source_name="droploo",
                external_id=f"SAMPLE-{1000 + idx_prod}",
                defaults={
                    "product": product,
                    "external_sku": pdata["sku_prefix"],
                    "external_slug": pdata["slug"],
                    "source_url": f"https://backend.droploo.com/api/product/details/{pdata['slug']}",
                    "sync_status": ProductSource.SyncStatus.SYNCED,
                    "last_synced_at": now_ts,
                    "is_development_fixture": True,
                    "raw_data": {
                        "sample_fixture": True,
                        "note": "Sample development fixture data clearly marked as non-production.",
                        "sku": pdata["sku_prefix"],
                    },
                },
            )


        # 6. Editorial & Policy ContentPages
        pages_spec = [
            (
                "about-us",
                "About DeenyaMart",
                "Curated modest apparel and mindful everyday essentials across Bangladesh.",
                "<p>DeenyaMart was founded to bring calm, transparent, editorial-grade online shopping to households across Bangladesh. We specialize in breathable modest garments—Linen Khimars, Pure Cotton Salat Hijabs, tailored Panjabis, and children's modest sets—alongside practical kitchen and home utility items.</p><p>Every garment is selected for opacity, breathability in humid weather, and honest sizing.</p>",
            ),
            (
                "return-policy",
                "Exchange & Return Policy",
                "Doorstep parcel inspection and 7-day size exchange or return.",
                "<p>We stand behind the fabric quality and stitching of every item we dispatch.</p><ul><li><strong>Doorstep Inspection:</strong> You may verify your parcel in the presence of the delivery rider before completing Cash on Delivery payment.</li><li><strong>7-Day Exchange Window:</strong> Unused, unwashed garments with original tags attached can be exchanged for size or returned within 7 days of delivery.</li></ul>",
            ),
            (
                "shipping-policy",
                "Delivery & Cash on Delivery Information",
                "Verified delivery timelines and transparent rates across all 64 districts.",
                "<p>DeenyaMart dispatches orders daily from Dhaka using verified courier partners.</p><ul><li><strong>Inside Dhaka City:</strong> ৳60 · 1–2 Business Days</li><li><strong>Dhaka Sub-Area (Savar, Keraniganj, Gazipur, Narayanganj):</strong> ৳100 · 2–3 Business Days</li><li><strong>Outside Dhaka (All 64 Districts):</strong> ৳120 · 3–5 Business Days</li></ul>",
            ),
            (
                "privacy-policy",
                "Privacy Policy",
                "How we protect your contact and delivery information.",
                "<p>We collect only the information required to fulfill and deliver your order (name, mobile number, delivery address, and optional email for seasonal dispatches). We never sell customer data to third parties.</p>",
            ),
            (
                "terms-and-conditions",
                "Terms & Conditions",
                "Storefront usage, pricing accuracy, and Cash on Delivery terms.",
                "<p>All prices are listed in Bangladeshi Taka (৳ / BDT). Promotional discounts reflect genuine reductions from documented regular retail prices.</p>",
            ),
        ]
        for slug, title, summary, body in pages_spec:
            ContentPage.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "summary": summary,
                    "body_html": body,
                    "is_active": True,
                },
            )

        self.stdout.write(self.style.SUCCESS("Seeded DeenyaMart categories, shipping zones, policies, and 12 development fixture products."))
