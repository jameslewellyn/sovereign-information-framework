# Data Model Reference

This document explains every data concept in the deals-tracker and how they
relate to each other.

---

## Core Insight: Unit Price is Everything

Pack prices are meaningless for comparison without context.

| Store   | Product              | Pack Price | Pack Size | Unit Price |
|---------|----------------------|-----------|-----------|------------|
| Costco  | Kirkland TP          | $22.99    | 30 rolls  | **$0.77/roll** |
| Target  | Charmin Ultra Strong | $15.99    | 12 rolls  | $1.33/roll |
| Walmart | Angel Soft           | $9.97     | 12 rolls  | $0.83/roll |
| Sam's   | Member's Mark TP     | $19.98    | 30 rolls  | **$0.67/roll** |

Costco looks expensive until you normalize. Sam's is cheapest by 13%.
This framework stores **both** pack price (what you pay) and unit price
(what you actually pay per unit of the thing you use).

---

## Data Flow

```
Adapters (flyers, product pages, Flipp API)
   │
   ▼
AdItem            ← raw scrape output from adapters
   │
   ▼  (unit_normalizer + catalog lookup)
PriceObservation  ← stored in DB, append-only
   │
   ├──► price_stats (updated per insert, rolling aggregates)
   │
   ▼
PriceComparator   ← cross-store unit price comparison
   │
   ▼
ComparisonRow     ← one product, all stores, with trend badges
   │
   ▼
EmailNotifier     → Digest email with deals + comparison table
```

---

## Models

### `PackSize`

Describes the physical quantity of a product as sold.

```python
PackSize(
    quantity=30,
    unit="roll",
    raw_description="30 Rolls",
)
```

Multi-level example (a "3-pack of 48 fl oz bottles"):
```python
PackSize(
    quantity=3,
    unit="pack",
    inner_quantity=48,
    inner_unit="fl oz",
    raw_description="3 × 48 fl oz",
)
# total_base_units = 3 × 48 = 144 fl oz
```

**Key property: `total_base_units`**
Converts everything to the canonical base unit (see table below) so
division gives a fair unit price.

#### Base Unit Conversions

| Input           | Base Unit | Multiplier    |
|-----------------|-----------|---------------|
| lb → oz         | oz        | × 16          |
| kg → oz         | oz        | × 35.274      |
| g  → oz         | oz        | × 0.035274    |
| l  → fl oz      | fl oz     | × 33.814      |
| ml → fl oz      | fl oz     | × 0.033814    |
| gal → fl oz     | fl oz     | × 128         |
| qt → fl oz      | fl oz     | × 32          |
| pt → fl oz      | fl oz     | × 16          |
| roll/load/count | same      | × 1           |

#### "Double/Triple Roll" handling

The `unit_normalizer` understands roll-size descriptors:
- "30 Double Rolls" → quantity = 30 × 2.0 = **60 rolls**
- "12 Mega Rolls"   → quantity = 12 × 4.0 = **48 rolls**

This prevents Bounty from gaming comparisons by labeling large rolls
as "each" or "mega".

---

### `PriceObservation`

The atomic unit of storage. **Never updated; only appended.**

```
id              — UUID
product_key     — e.g. "paper-goods/toilet-paper-bulk"
canonical_name  — from catalog, e.g. "Toilet Paper (Bulk)"
category        — e.g. "Paper Goods"

store           — e.g. "Costco East Peoria"
chain           — e.g. "Costco"

product_name    — as listed in the ad
pack_size       — PackSize object
pack_price      — what you pay at the register ($22.99)
unit_price      — computed: pack_price / total_base_units ($0.77/roll)
base_unit       — e.g. "roll"
price_type      — "sale" | "everyday"

original_pack_price  — regular price if this is a sale
original_unit_price  — computed from original_pack_price
discount_pct         — computed or from flyer

valid_from / valid_until  — for sale items
source          — adapter name ("flipp", "product_page", "pdf_flyer")
observed_at     — UTC timestamp
```

Every time the scheduler runs and scrapes a price, a new row is inserted.
Historical rows are never touched. This gives you a complete timeline.

---

### `PriceStats`

Aggregated summary per `(product_key, store)` pair.
Recomputed automatically when new observations are saved.

```
current_pack_price / current_unit_price    ← most recent scrape
avg/min/max_unit_price_30d                 ← rolling 30-day stats
avg/min/max_unit_price_90d                 ← rolling 90-day stats
min_unit_price_ever                        ← all-time low reference

vs_30d_avg_pct    ← % above or below 30d average
                     -25 means "25% cheaper than usual" → great deal
                     +20 means "20% more expensive than usual" → avoid

price_trend       ← "rising" | "falling" | "stable"
                     computed from last 5 observations

is_all_time_low   ← True if current unit price ≤ ever-min × 1.01
```

`PriceStats` powers the trend badges in the email digest and the
"vs 30d avg" labels on deal rows.

---

### `ComparisonRow`

A cross-store comparison for one product.

```
product_key     "paper-goods/toilet-paper-bulk"
canonical_name  "Toilet Paper (Bulk)"
base_unit       "roll"

unit_prices:  {"Costco": 0.77, "Walmart": 0.83, "Sam's": 0.67}
pack_info:    {"Costco": "30 rolls @ $22.99", ...}
best_store:   "Sam's"
best_unit_price:  0.67
worst_unit_price: 0.83
unit_savings:     0.16      ← per roll, Sam's vs Walmart
pct_savings:      19.3%

stats: {store → PriceStats}   ← for trend badges
```

The email comparison table renders one row per `ComparisonRow`,
with cells sorted by store column.

---

## Database Tables

### `price_observations` — append-only history

```sql
product_key, store, pack_price, unit_price, base_unit,
pack_description, price_type, observed_at, ...
```

Never updated. Query this for:
- Price history charts
- Trend analysis
- Shrinkflation detection
- Audit trail

### `price_stats` — mutable rolling aggregates

```sql
PRIMARY KEY (product_key, store)
current_unit_price, avg_unit_price_30d, min_unit_price_90d,
vs_30d_avg_pct, price_trend, is_all_time_low, ...
```

Updated atomically after each batch insert. Query this for:
- Quick "is this a good deal?" answer
- Dashboard / comparison table rendering

### `schema_version` — migration tracking

```sql
version INTEGER PRIMARY KEY
applied_at TIMESTAMP
description VARCHAR
```

Enables safe schema evolution. Current version: 3.

### `products` — product catalog

```sql
product_key VARCHAR PRIMARY KEY
canonical_name, category, subcategory, base_unit
```

Populated from `conf/products/catalog.yml`.

---

## Trend Detection

### Normal price trend

Computed from the last 5 unit-price observations:
- **falling**: current < oldest × 0.97 (dropped >3%)
- **rising**:  current > oldest × 1.03 (rose >3%)
- **stable**:  otherwise

### Shrinkflation detection

`PriceComparator.detect_shrinkflation()` looks for:
> Pack price stayed flat, but unit price rose >5%

This means the pack got smaller without the sticker price changing.
Classic example: coffee going from 40oz → 34oz at the same $12.99.

### Fake sale detection

`PriceComparator.detect_fake_sale()` flags items where:
> The item is marked "sale" but current unit price ≥ 30-day average

This indicates the store raised the price beforehand to manufacture
a fake discount (common in supermarket flyer culture).

---

## Unit Normalizer

`src/storage/unit_normalizer.py` — parses free-text pack strings.

```python
from src.storage.unit_normalizer import parse_pack_size, enrich_with_unit_price

ps = parse_pack_size("30 Double Rolls")
# PackSize(quantity=60.0, unit="roll")
# ps.total_base_units == 60.0

unit_price, base_unit, pack = enrich_with_unit_price(22.99, "30 rolls")
# (0.7663, "roll", PackSize(quantity=30, unit="roll"))
```

Handles all common patterns:
- `"30 rolls"`, `"2.5 lbs"`, `"146 loads"`, `"64 fl oz"`
- `"3-pack of 48 fl oz each"`, `"4 × 4.1 oz"`
- `"4.1 oz, 4-pack"`, `"16 Count"`
- `"30 Double Rolls"`, `"12 Mega Rolls"`
- `"48 oz / 3 lb"` (takes first match)

Returns `None` for unparseable strings so the system degrades gracefully.

---

## Email Digest Layout

```
🛒 Weekly Price Digest — Friday Mar 06, 2026
32 matched deals · stores: Costco East Peoria, Walmart, Aldi

📋 This Week's Matched Deals
┌─────────────────────────────┬──────────────┬────────────────────┬─────────────┬────────────────┬─────────────┐
│ Product                     │ Pack Size    │ Store              │ Pack Price  │ Unit Price     │ Valid Until │
├─────────────────────────────┼──────────────┼────────────────────┼─────────────┼────────────────┼─────────────┤
│ PAPER GOODS                                                                                                  │
│ Kirkland TP [ALL TIME LOW]  │ 30 rolls     │ Costco East Peoria │ ~~$25.99~~ $22.99 ↓12% │ $0.767/roll │ Apr 01  │
│ Bounty Full Sheet           │ 12 ct        │ Aldi               │ $8.49      │ $0.708/sheet   │ Mar 08      │
├─────────────────────────────┼──────────────┼────────────────────┼─────────────┼────────────────┼─────────────┤
│ COFFEE                                                                                                       │
│ Lavazza Espresso [31% below 30d avg] │ 2.5 lb  │ Costco  │ $12.99  │ $5.20/lb    │ Mar 15      │

📊 Cross-Store Price Comparison
All prices shown as unit price ($/roll, $/oz, etc.)
Green = cheapest · ↑ rising · ↓ falling · ★ all-time low · hover for pack details.

┌─────────────────────┬──────┬─────────────┬──────────────┬────────┬────────────────────────────┐
│ Product             │ Unit │ Costco      │ Walmart      │ Sam's  │ Savings vs Worst           │
├─────────────────────┼──────┼─────────────┼──────────────┼────────┼────────────────────────────┤
│ PAPER GOODS                                                                                     │
│ Toilet Paper (Bulk) │/roll │ $0.767 ↓   │ $0.831 ↑    │$0.667★│ save $0.164/roll (20%)      │
│ Paper Towels        │/sheet│ $0.056     │ $0.061       │$0.052 │ save $0.009/sheet (15%)      │
│ COFFEE                                                                                           │
│ Ground Coffee       │/oz   │ $0.325 →   │ $0.531       │  —    │ save $0.206/oz (39%)         │
```
