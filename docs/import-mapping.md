# DeenyaMart Product Import Mapping & Operations Guide

## 1. Source Verification & Integration Limitations (`https://droploo.com/`)

1. **Inspection Findings**:
   - `https://droploo.com/` (and `https://droploo.com/robots.txt`) serves an unrendered client-side React/Vite Single Page Application shell (`<div id="root"></div>`).
   - While the frontend application communicates with `https://backend.droploo.com/api/*` to render storefront views, Droploo does **not** publish a self-serve public API specification, Terms-of-Service-authorized public supplier feed, or unauthenticated downloadable CSV/Excel/JSON catalog export.
   - Accessing merchant/dropshipper product exports or partner credentials on Droploo requires an authenticated merchant account and explicit supplier authorization.
2. **Architectural Decision & Guardrails**:
   - **No Unverified Live Sync**: Because an authorized B2B partner API integration cannot be verified without merchant credentials and explicit supplier permission, **real-time stock synchronization (`--sync`) is disabled** and not promised. Invoking `python manage.py import_products --sync` exits safely with a `CommandError` explaining this limitation.
   - **No Fabricated Data or Bypassed Protections**: No API endpoints, credentials, product records, stock counts, or product images are invented, and no access controls or anti-bot protections are bypassed.
   - **Reusable CSV & JSON Import Pipeline**: Instead, DeenyaMart provides a deterministic, idempotent file-based import pipeline (`python manage.py import_products --file <path>`) supporting both a documented CSV schema and JSON catalog exports.

---

## 2. Separation of Supplier Cost, Customer Selling Price, and Margin

DeenyaMart strictly separates **supplier wholesale cost** from **customer retail selling price**:

| Concept | CSV / JSON Field(s) | Target Model Field | Storefront Visibility & Validation Rule |
| :--- | :--- | :--- | :--- |
| **Supplier Cost** | `supplier_cost` *(aliases: `wholesale_price`, `buy_price`, `cost_price`)* | `Product.wholesale_cost_price` (`product.supplier_cost`), `ProductVariant.wholesale_price`, `SupplierProductMap.supplier_cost` | **Internal admin only.** Never exposed as customer retail price. |
| **Customer Selling Price** | `selling_price` *(aliases: `retail_price`, `customer_price`, `store_price`, `price`)* | `Product.selling_price`, `ProductVariant.retail_price` | **Active storefront price in `BDT`.** Must be explicitly provided OR computed from `supplier_cost` using an explicit `margin_percent` / `markup_percent`. |
| **Compare-At Price** | `compare_at_price` *(aliases: `original_price`, `strikethrough_price`, `mrp`)* | `Product.compare_at_price`, `ProductVariant.compare_at_price` | Optional strikethrough price. Validated so `compare_at_price >= selling_price`. Values of `0` or equal to `selling_price` normalize to `None` (no discount badge). |
| **Gross Margin %** | `margin_percent` *(or CLI `--margin-percent`)* | `Product.margin_percentage` (computed property), `SupplierProductMap.applied_margin_percent` | Formula: $\text{selling\_price} = \frac{\text{supplier\_cost}}{1 - (\text{margin\_percent} / 100)}$. Validated in range $[0, 99.99]$. |
| **Markup %** | `markup_percent` *(or CLI `--markup-percent`)* | `Product.markup_percentage` (computed property) | Formula: $\text{selling\_price} = \text{supplier\_cost} \times (1 + \text{markup\_percent} / 100)$. |

### Critical Pricing Guardrails
- **Supplier Cost Is Never Assumed to Be Retail Price**: If a new product row supplies `supplier_cost` (or `supplier_regular_price`) without `selling_price` and without a `margin_percent` or `markup_percent`, the importer **rejects the row** with a clear `selling_price` validation error.
- **Negative Margin Protection**: If `selling_price < supplier_cost`, the row is rejected unless `--allow-negative-margin` is explicitly passed on the command line.

---

## 3. Documented CSV Schema & Field Mapping

### 3.1 Supported CSV Columns

| Column Header | Aliases | Required? | Type / Format | Mapping & Non-Destructive Blank Rule |
| :--- | :--- | :--- | :--- | :--- |
| `external_id` | `external_product_id`, `source_id`, `id` | **Yes** *(or `sku`)* | String / Integer (`DRP-1001`, `1738`) | Maps to `ProductSource.external_id` and `SupplierProductMap.external_product_id` for idempotent upserts. |
| `sku` | `sku_prefix`, `product_code` | **Yes** *(or `external_id`)* | String (`KCK114`, `LF104`) | Maps to `Product.sku` and `Product.sku_prefix`. Checked for in-file and DB-level duplicate SKU conflicts. |
| `name` | `product_name`, `title` | **Yes** *(on create)* | String (`Kids Lilen Khimar Set`) | Maps to `Product.name`. On update, blank preserves existing name unless `--overwrite-blank` is set. |
| `category` | `category_name`, `category_slug` | **Yes** *(on create)* | String (`Women's Modest Wear` or `Women's Modest Wear > Woman Khimar`) | Resolved via `SupplierCategoryMap`, built-in typo normalization (`"Man's Cothing"` → `"Men's Modest Wear"`), or `Category` slug/name. |
| `subcategory` | `subcategory_name`, `subcategory_slug` | Optional | String (`Woman Khimar`) | Optional child category under `category`. |
| `external_category_id` | `cat_id` | Optional | String / Integer (`57`) | Matched against `SupplierCategoryMap.external_category_id`. |
| `external_subcategory_id` | `sub_cat_id` | Optional | String / Integer | Matched against `SupplierCategoryMap.external_subcategory_id`. |
| `brand` | `brand_name`, `fabric` | Optional | String (`Natural Linen`) | Maps to `Brand`. Omitted/blank values preserve existing brand on update. |
| `currency` | `currency_code` | Optional | ISO Code / Symbol (`BDT`, `৳`, `Tk`) | Explicitly validated against `settings.STORE_CURRENCY_CODE` (`BDT`). Mismatched currencies (`USD`, `EUR`) are rejected. |
| `supplier_cost` | `wholesale_price`, `buy_price`, `cost_price` | Optional | Decimal (`420.00`, `৳420`) | Stored in `Product.wholesale_cost_price` and `SupplierProductMap.supplier_cost`. |
| `selling_price` | `retail_price`, `customer_price`, `price` | **Yes** *(unless cost + margin/markup given)* | Positive Decimal (`650.00`, `BDT 650.00`) | Stored in `Product.selling_price`. Must be `> 0` and `>= supplier_cost`. |
| `compare_at_price` | `original_price`, `mrp` | Optional | Decimal (`790.00`) | Stored in `Product.compare_at_price` only if `> selling_price`. |
| `margin_percent` | `margin_percentage` | Optional | Decimal (`35.00` or `35%`) | Used to derive `selling_price` from `supplier_cost` when `selling_price` is blank. |
| `markup_percent` | `markup_percentage` | Optional | Decimal (`50.00` or `50%`) | Used to derive `selling_price` from `supplier_cost` when `selling_price` is blank. |
| `stock` | `stock_qty`, `stock_quantity`, `qty` | Optional | Non-negative Integer (`15`, `0`) | **Only applied when supplied.** If omitted on a new product, `track_inventory=False` and `stock_qty=0` (never fabricates stock). If omitted on update, preserves existing stock. |
| `short_description` | `summary` | Optional | Plain text | Maps to `Product.short_description`. Preserved on update if blank. |
| `description` | `long_description`, `description_html` | Optional | Text or HTML | Sanitized HTML stored in `Product.description_html` and plain text in `Product.description`. Preserved on update if blank. |
| `fabric_composition` | `material` | Optional | String (`100% Pure Cotton`) | Maps to `Product.fabric_composition`. Preserved on update if blank. |
| `images` | `image_url`, `image_urls`, `primary_image` | Optional | Pipe/semicolon-separated URLs or JSON list | **Only created when supplied.** First valid image is marked `is_primary=True`. Unavailable/broken URLs are logged as row warnings and skipped safely. |
| `variants` | `variants_json` | Optional | JSON array or compact `SKU:Size:Color:Stock:SellingPrice[:SupplierCost]` separated by `;` | **Only created when supplied.** If omitted on a new product, `has_variants=False` and no `ProductVariant` rows are fabricated. |
| `source_url` | `external_url` | Optional | URL | Stored on `ProductSource.source_url`. |

---

### 3.2 Example CSV File (`products.csv`)

```csv
external_id,sku,name,category,subcategory,brand,currency,supplier_cost,selling_price,compare_at_price,stock,short_description,description,images,variants
DRP-2001,KCK114,Kids Linen Khimar Set KCK114,Kids' Modest Wear,Kids Khimar,Natural Linen,BDT,420.00,650.00,750.00,14,"Lightweight two-piece kids khimar set.","<p>Breathable junior linen khimar set for ages 5 to 10.</p>",/static/img/fixtures/kck114-primary.svg|/static/img/fixtures/kck114-secondary.svg,"KCK114-56:Age 5-6:Terracotta:6:650.00:420.00;KCK114-78:Age 7-8:Terracotta:8:650.00:420.00"
DRP-2002,HSR301,Brass Counter Spice Rack HSR301,Home & Kitchen,Kitchen Organizers,Studio Utility,BDT,600.00,980.00,1150.00,8,"Two-tier countertop spice rack.","Freestanding powder-coated steel and brass spice rack.",/static/img/fixtures/hsr301-primary.svg,
```

### 3.3 Example JSON File (`products.json`)

```json
{
  "products": [
    {
      "external_id": "DRP-3001",
      "sku": "VCP108",
      "name": "Embroidered Cotton Panjabi VCP108",
      "category": "Men's Modest Wear",
      "subcategory": "Panjabi",
      "brand": "Pure Cotton",
      "currency": "BDT",
      "supplier_cost": "780.00",
      "selling_price": "1250.00",
      "compare_at_price": "1450.00",
      "short_description": "Relaxed-fit combed cotton panjabi.",
      "images": [
        "/static/img/fixtures/vcp108-primary.svg",
        "/static/img/fixtures/vcp108-secondary.svg"
      ],
      "variants": [
        {
          "sku": "VCP108-M",
          "size": "M (40)",
          "color": "Deep Slate",
          "supplier_cost": "780.00",
          "selling_price": "1250.00",
          "stock": 5
        },
        {
          "sku": "VCP108-L",
          "size": "L (42)",
          "color": "Deep Slate",
          "supplier_cost": "780.00",
          "selling_price": "1250.00",
          "stock": 4
        }
      ]
    }
  ]
}
```

---

## 4. Non-Destructive Update Rule (`--overwrite-blank`)

By default (`overwrite_blank=False`):
- Rerunning `import_products` matches existing records by `(source_name, external_id)` (or development fixture SKU) and updates them idempotently without creating duplicate products.
- **Absent or empty optional columns in the import file NEVER overwrite valid existing data** on the product (`description`, `short_description`, `brand`, `compare_at_price`, `supplier_cost`, `stock`, `images`, `variants`, or `category`).
- If you explicitly want blank fields in the import file to clear existing product fields, pass `--overwrite-blank`.

---

## 5. Authorized Image Downloading & Security Controls

External image downloading is **off by default** and requires explicit authorization via both environment variables and CLI flags:

1. **Environment Variables (`config/settings/base.py`)**:
   - `IMPORT_ALLOW_IMAGE_DOWNLOAD=True`: Master switch authorizing external HTTP(S) image downloads.
   - `IMPORT_ALLOWED_IMAGE_HOSTS=backend.droploo.com,droploo.com`: Comma-separated hostname allowlist. Loopback (`localhost`, `127.0.0.1`) and private/link-local IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`) are always blocked to prevent SSRF.
   - `IMPORT_IMAGE_TIMEOUT_SECONDS=10`: Per-image network socket timeout.
   - `IMPORT_MAX_IMAGE_BYTES=5242880`: Maximum allowed file size (default 5 MB).
2. **Validation & Failure Handling**:
   - Verifies both the HTTP `Content-Type` header (`image/jpeg`, `image/png`, `image/webp`, `image/gif`) and the binary magic bytes of the downloaded payload.
   - Sanitizes filenames to lowercase alphanumeric + hyphens with a deterministic hash (`<sku>-<stem>-<hash>.<ext>`), preventing path traversal.
   - If an image fails to download (404, timeout, invalid MIME type, or exceeds size limit), the failure is logged in `ImportJobLog.row_warnings` and the product still imports safely without broken image records.

---

## 6. Setup & Command Usage Instructions

### Step 1: Configure Environment Variables
Set your environment variables (or `.env`) before running imports:
```powershell
$env:STORE_CURRENCY_CODE = "BDT"
# Optional: enable only when downloading images from an authorized supplier host
$env:IMPORT_ALLOW_IMAGE_DOWNLOAD = "True"
$env:IMPORT_ALLOWED_IMAGE_HOSTS = "backend.droploo.com,droploo.com"
```

### Step 2: Run in Dry-Run Mode First
Always validate your CSV or JSON file with `--dry-run` first to inspect row-level errors, duplicate SKUs, and margin checks without modifying the database:
```powershell
python manage.py import_products --file path/to/products.csv --dry-run
```

### Step 3: Run the Live Import
Once dry-run reports zero errors:
```powershell
# Standard import with explicit selling_price column in CSV/JSON:
python manage.py import_products --file path/to/products.csv

# Or compute customer selling_price automatically from supplier_cost using a 40% markup:
python manage.py import_products --file path/to/products.csv --markup-percent 40

# Or compute customer selling_price from supplier_cost targeting a 30% gross margin:
python manage.py import_products --file path/to/products.json --margin-percent 30

# Include authorized image downloading:
python manage.py import_products --file path/to/products.csv --download-images
```

### Step 4: Inspect Import Logs
Every run (including `--dry-run`) creates an `ImportJobLog` record with `started_at`/`finished_at` timestamps, summary counts, `row_warnings`, and `row_errors`, viewable in Django Admin under **Catalog Ingestion Pipeline → Import job logs**.
