# Architecture Overview

## Two Data Pipelines

The system runs two distinct pipelines on separate schedules:

```
┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE 1 — FLYER / AD DEALS  (weekly, Tue/Wed)                  │
│                                                                     │
│  Flipp API ──┐                                                      │
│  Playwright ─┼──► AdItem (price_type="sale") ──► BrandMatcher      │
│  PDF Flyer ──┘         │                              │             │
│                   DB: ad_items              MatchedDeals            │
│                   DB: matched_deals              │                  │
│                                             Email Digest            │
│                                          (deals section)            │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  PIPELINE 2 — EVERYDAY PRICES  (weekly, Sunday)                     │
│                                                                     │
│  conf/products/catalog.yml                                          │
│       │  (product URLs per store)                                   │
│       ▼                                                             │
│  ProductPageAdapter ──► AdItem (price_type="everyday")             │
│       │                         │                                   │
│       │                  DB: price_records                          │
│       │                         │                                   │
│       └──────────────► PriceComparator ──► ComparisonRows           │
│                                                │                    │
│                                          Email Digest               │
│                                       (comparison table)            │
└─────────────────────────────────────────────────────────────────────┘
```

Both pipelines feed into the **same weekly email digest**:

```
🛒 Weekly Deals Digest — Tuesday Mar 10

SECTION 1: This Week's Deals
  (from flyer adapters — matched against brand watchlist)
  MEAT      Maple Leaf Bacon 375g    Safeway    $3.99  ↓43%
  COFFEE    Lavazza Espresso 1kg     No Frills  $12.99 ↓28%

SECTION 2: Price Comparison Across Stores
  (from product page scrapes — everyday shelf prices)
                       Costco   Walmart  Target   Sam's   Savings
  Toilet Paper 30pk   $22.99   $18.99   $24.99   $21.49  $6.00
  Ground Beef /lb     $4.99    $5.49    $6.29    $4.79   $1.50
  Ground Coffee 3lb   $14.99   $12.49   $16.99   $11.99  $5.00
```

---

## Component Map

```
deals-tracker/
│
├── conf/
│   ├── brands/
│   │   └── watchlist.yml        ← categories + acceptable brands + filters
│   ├── products/
│   │   └── catalog.yml          ← product page URLs per store for everyday prices
│   └── adapters/
│       ├── costco_east_peoria.yml    ← Flipp, Tue schedule
│       ├── product_pages.yml         ← ProductPageAdapter, Sun schedule
│       └── *.yml                     ← one per store/source
│
├── src/
│   ├── models.py                ← AdItem, PriceRecord, ComparisonRow, Digest
│   ├── scheduler.py             ← APScheduler entry point, routes to correct runner
│   │
│   ├── adapters/
│   │   ├── base.py              ← SourceAdapter ABC
│   │   ├── __init__.py          ← registry: adapter name → class
│   │   ├── flipp.py             ← Flipp public API
│   │   ├── playwright_generic.py ← any store website via browser
│   │   ├── pdf_flyer.py         ← PDF/image flyer download + parse
│   │   └── product_page.py      ← individual product URLs → everyday price
│   │
│   ├── parsers/
│   │   ├── llm_parser.py        ← Instructor + any LLM (Ollama/OpenAI/Anthropic)
│   │   └── css_parser.py        ← CSS selector extraction, no LLM
│   │
│   ├── matchers/
│   │   └── brand_matcher.py     ← filters AdItems against watchlist.yml
│   │
│   ├── comparators/
│   │   └── price_comparator.py  ← cross-store price comparison from price_records
│   │
│   ├── storage/
│   │   └── db.py                ← DuckDB: ad_items, matched_deals, price_records
│   │
│   └── notifiers/
│       └── email_notifier.py    ← HTML email with deals + comparison table
│
└── data/
    └── deals.duckdb             ← all historical price data (git-ignored)
```

---

## Data Flow Details

### AdItem.price_type
Every price observation is tagged:
- `"sale"` — from a flyer/ad, limited time, has valid_until
- `"everyday"` — scraped from a product page, reflects normal shelf price

Both land in `ad_items` table. Only `"everyday"` items also go into `price_records`
with a `product_key` from the catalog, enabling structured comparison.

### product_key
Hierarchical slug from `catalog.yml`:
```
"paper-goods/toilet-paper-bulk"
"meat/chicken-breast-lb"
"dairy/eggs-dozen"
```
Used as the primary grouping key in `price_records` so the same product across
stores can be compared even when product names differ ("Kirkland Bath Tissue" vs
"Member's Mark Ultra Premium").

### Schedules
| Adapter | Default Schedule | Rationale |
|---------|-----------------|-----------|
| Flipp (Costco, etc.) | Tuesday 6am | Flyers refresh Tue/Wed |
| Playwright/PDF stores | Wednesday 7am | Some stores update Wed |
| Product pages | Sunday 4am | Everyday prices change slowly |

All are configurable per adapter YAML.

---

## Adding a New City

Add one config file per store in that city:

```yaml
# conf/adapters/walmart_peoria.yml
adapter: flipp
enabled: true
schedule: "0 6 * * 2"
postal_code: "61602"     # Peoria IL downtown
stores:
  - walmart
  - aldi
  - meijer
```

Add product URLs for that city's stores to `catalog.yml`:

```yaml
toilet-paper-bulk:
  stores:
    Meijer: "https://www.meijer.com/shopping/product/..."
    Aldi:   "https://www.aldi.us/..."
```

The comparator automatically includes all stores that have price records — 
no other code changes needed.

---

## Adding a New Store Website (Playwright)

1. Create `conf/adapters/mystore.yml` with `adapter: playwright_generic`
2. Inspect the store's weekly ad page in DevTools, find CSS selectors for
   product tiles, name, price
3. Set `parse_strategy: css_only` with the selectors, or use `crawl4ai_llm`
   to let the LLM figure it out without selectors

---

## Email Digest Preview

```
Subject: 🛒 14 deals + price comparison (Mar 10)

═══════════════════════════════════════════════════════════
This Week's Deals
═══════════════════════════════════════════════════════════
MEAT
  Maple Leaf Bacon 375g      Safeway      $3.99  ↓43%  valid Mar 16
  Schneiders Wieners         Walmart      $2.49  ↓38%  valid Mar 15

COFFEE
  Lavazza Espresso 1kg       No Frills   $12.99  ↓28%  valid Mar 14

═══════════════════════════════════════════════════════════
Price Comparison Across Stores  (green = cheapest)
═══════════════════════════════════════════════════════════
                         Costco   Walmart  Target  Sam's  Savings
Paper Goods
  Toilet Paper ~30 rolls  $22.99  $18.99   $24.99  $21.49  $6.00 ←
  Paper Towels 12 rolls   $19.99  $17.49   $21.99  $18.99  $4.50 ←

Meat
  Chicken Breast /lb       $3.99   $4.49    $5.29   $3.79  $1.50 ←
  Ground Beef 80/20 /lb    $4.99   $5.49    $6.29   $4.79  $1.50 ←

Dairy
  Whole Milk gallon        $4.49   $3.49    $4.99     —    $1.50 ←
  Eggs dozen               $3.99   $2.99    $4.49   $2.89  $1.60 ←
```
