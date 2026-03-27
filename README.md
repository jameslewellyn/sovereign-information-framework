# deals-tracker

Automated store deal finder. Scrapes flyers periodically, matches against your
brand/category watchlist, and emails a digest summary.

```
Adapters (pluggable, one per store)
    │  Flipp API · Playwright scraper · PDF flyer
    ▼
AdItems (normalised)
    │
    ▼
BrandMatcher  ←  conf/brands/watchlist.yml
    │
    ▼
MatchedDeals
    │
    ├──▶ DuckDB (history)
    └──▶ Email Digest
```

---

## Project Structure

```
deals-tracker/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── conf/
│   ├── brands/
│   │   └── watchlist.yml          ← your acceptable brands per category
│   └── adapters/
│       ├── flipp.yml              ← Flipp API (Walmart, Safeway, etc.)
│       ├── kroger_playwright.yml  ← Playwright scraper (disabled by default)
│       └── pdf_flyer.yml          ← PDF flyer parser (disabled by default)
├── src/
│   ├── models.py                  ← shared data models (AdItem, Digest, etc.)
│   ├── scheduler.py               ← APScheduler entry point
│   ├── adapters/
│   │   ├── base.py                ← SourceAdapter ABC
│   │   ├── __init__.py            ← adapter registry
│   │   ├── flipp.py
│   │   ├── playwright_generic.py
│   │   └── pdf_flyer.py
│   ├── parsers/
│   │   ├── llm_parser.py          ← Instructor + any LLM
│   │   └── css_parser.py          ← CSS-only, no LLM
│   ├── matchers/
│   │   └── brand_matcher.py
│   ├── storage/
│   │   └── db.py                  ← DuckDB
│   └── notifiers/
│       └── email_notifier.py
└── data/                          ← DuckDB database (git-ignored)
```

---

## Setup

### 1. Configure secrets

```bash
cp .env.example .env
# edit .env — set SMTP credentials and NOTIFY_EMAIL
```

### 2. Configure your watchlist

Edit `conf/brands/watchlist.yml`:

```yaml
categories:
  meat:
    priority: 3
    min_discount: 20
    brands:
      - Maple Leaf
      - Schneiders
  coffee:
    priority: 3
    brands:
      - Lavazza
      - Kicking Horse
```

### 3. Configure adapters

Edit `conf/adapters/flipp.yml` — set your postal code:

```yaml
postal_code: "90210"
stores:
  - walmart
  - kroger
```

Enable/disable adapters by setting `enabled: true/false` in each yml file.

### 4. Run

```bash
# with Docker (recommended)
docker compose up -d

# locally
pip install -r requirements.txt
playwright install chromium
python -m src.scheduler
```

---

## Adding a New Store

1. Create `src/adapters/mystore.py` — subclass `SourceAdapter`, implement `fetch()`
2. Create `conf/adapters/mystore.yml` — set adapter name, schedule, and any store-specific config
3. Register the class in `src/adapters/__init__.py`

Nothing else changes. The scheduler, matcher, notifier, and DB are all unaware of
which adapters exist — they only know about `AdItem`.

### Minimal adapter template

```python
from src.adapters.base import SourceAdapter
from src.models import AdItem

class MyStoreAdapter(SourceAdapter):
    name = "mystore"

    async def fetch(self) -> list[AdItem]:
        # fetch + normalise → return list[AdItem]
        ...
```

---

## Changing Parse Strategy Per Store

Each Playwright/PDF adapter config has a `parse_strategy` field:

| Strategy | Speed | Cost | Requires |
|----------|-------|------|---------|
| `css_only` | Instant | Free | Known CSS selectors |
| `crawl4ai_llm` | Fast | LLM tokens | Crawl4AI + model |
| `ollama_vision` | Medium | Free | Ollama running locally |

Change it in the adapter's `.yml` file without touching any Python code.

---

## Email Digest Example

```
🛒 Weekly Deals Digest — Tuesday Mar 10, 2026
14 deals found across: Walmart, Safeway, No Frills

MEAT
  Maple Leaf Bacon 375g    Safeway    $3.99  (was $6.99 ↓43%)   valid until Mar 16
  Schneiders Wieners       Walmart    $2.49  (was $3.99 ↓38%)   valid until Mar 15

COFFEE
  Lavazza Espresso 1kg     No Frills  $12.99 (was $17.99 ↓28%)  valid until Mar 14
```

---

## Querying History

```python
import duckdb
conn = duckdb.connect("data/deals.duckdb")

# best deals this week
conn.execute("""
    SELECT product_name, store, sale_price, discount_pct
    FROM matched_deals
    WHERE scraped_at > now() - interval '7 days'
    ORDER BY discount_pct DESC
    LIMIT 20
""").fetchdf()

# price history for a product
conn.execute("""
    SELECT scraped_at::date, store, sale_price
    FROM ad_items
    WHERE product_name ILIKE '%lavazza%'
    ORDER BY scraped_at
""").fetchdf()
```
