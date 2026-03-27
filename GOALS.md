# Project Goals — deals-tracker

## The Problem

Grocery and household prices vary significantly across stores in the same city,
and they change constantly. Finding the best price requires visiting multiple
store websites, comparing packs of different sizes, and remembering what
something usually costs — none of which most people have time to do.

On top of that, stores use tactics that make comparison deliberately difficult:

- **Pack size variation** — "$9.99 for 18 rolls" vs "$22.99 for 30 rolls" are
  impossible to compare without doing the math.
- **Fake sales** — prices are quietly raised before being "discounted" back.
- **Shrinkflation** — the same product silently gets smaller while the price
  stays the same.
- **Flyer overload** — weekly ads contain hundreds of items but only a handful
  are genuinely good deals.

## The Goal

Build a **personal, self-hosted price intelligence system** that:

1. **Tracks prices automatically** across every major store in your city, for
   the products you actually buy.

2. **Normalizes everything to unit price** so comparisons are always fair —
   `$/roll`, `$/oz`, `$/load` — regardless of how each store packages the item.

3. **Builds a historical record** so the system knows what a normal price looks
   like and can flag when something is genuinely cheap or suspiciously expensive.

4. **Sends a weekly email digest** summarizing matched deals and a cross-store
   price comparison table — so you get actionable insight without having to
   think about it.

5. **Stays personal** — your store list, your brands, your products. Nothing is
   shared or uploaded anywhere. All data lives in a local database on your
   machine.

## Design Principles

### Unit price is the only honest comparison metric

Two products cannot be fairly compared by pack price alone. The system converts
every observation to a `unit_price` (e.g. `$0.77/roll`) using a normalizer that
understands pack descriptions like "30 Double Rolls", "3-pack of 48 fl oz", and
"2.5 lb bag". This is the number used for all comparisons, rankings, and alerts.

### History enables intelligence

A price scraped once is a data point. A price scraped weekly for six months is
a signal. The database is append-only — every observation is kept forever.
This enables:

- Detecting when a "sale" price is actually the normal price
- Identifying genuine all-time lows
- Tracking trends (rising / falling / stable)
- Spotting shrinkflation (same price, smaller pack → higher unit price)

### Modular by design

Each store is a separate adapter with its own config file. Different stores
require different scraping strategies — Flipp API for weekly flyers, direct
product page scraping with LLM parsing for everyday prices, PDF parsing for
printed ads. Adding a new store means adding one config file and optionally one
adapter module, nothing else.

### Two data pipelines, one digest

**Flyer pipeline** — runs weekly, captures promotional sale prices from store
flyers. Filters against your brand/category watchlist and highlights the deals
that matter to you.

**Everyday price pipeline** — runs on a slower schedule, scrapes product pages
for current shelf prices. Enables fair cross-store comparison even when nothing
is on sale.

Both feed into a single email digest with two sections: this week's matched
deals, and a running price comparison table across all stores.

### Local-first, privacy-preserving

- All data stored in a local DuckDB file (`data/deals.duckdb`)
- No external accounts, no cloud sync, no telemetry
- Personal config (stores, brands, URLs) is gitignored and never committed
- LLM parsing can run entirely locally via Ollama — no API keys required

## Future Sales — Don't Buy Today What's Cheaper Next Week

Stores distribute flyers 3–7 days before sales begin. This means every Tuesday
scrape contains deals that don't start until Thursday or Friday. Without
tracking `valid_from` dates, that information is wasted.

The system captures this explicitly with a **sale status** on every price observation:

| Status | Meaning |
|---|---|
| `upcoming` | Sale is in a flyer but hasn't started yet — `valid_from` is in the future |
| `active` | Sale is running right now — `valid_from ≤ today ≤ valid_until` |
| `expired` | Sale window has closed — kept in history for trend analysis |
| `everyday` | Standard shelf price — no promotional window |

### WaitSignal — the "don't buy yet" alert

A **WaitSignal** is generated when an upcoming sale price is meaningfully
cheaper (≥8% unit-price improvement) than what you'd pay at the best available
everyday price today.

Example:

> **⏳ Whole Milk — wait 3 days**  
> Kroger now: $3.39/gal → Aldi sale starts Thu: $2.49/gal  
> Save $0.90/gal (27%) — valid Thu Feb 27 – Wed Mar 5

WaitSignals appear:
- As a yellow banner at the top of the weekly email digest
- Inline in `price_check.py` when querying a specific product
- Via `python scripts/price_check.py --wait` for a full list

### Upcoming Sales calendar

Below the WaitSignal banner, the digest shows a full calendar of future sales —
all deals currently in flyers that haven't started yet. This lets you plan the
week's shopping run in advance rather than reacting after the fact.

---

## What Success Looks Like

You open your weekly email and see:

> **Kirkland TP @ Costco — $0.67/roll** ✓ cheapest  
> ↓ 14% below 30-day average · all-time low  
> vs Target $0.91/roll · Walmart $0.83/roll

You do not need to visit any store websites. You did not have to think about
pack sizes. You know immediately whether to buy now or wait.

Over time the system also tells you:

> **Folgers 43.5 oz @ Walmart** — price has risen 18% over the last 90 days  
> **Angel Soft TP** — pack shrunk from 18 to 16 rolls at the same $8.99 price  
> **Aldi whole milk** — has been the cheapest in Peoria every single week for 6 months

## Scope

**In scope:**
- Grocery staples and household consumables (paper goods, cleaning, personal
  care, dairy, meat, pantry, frozen, beverages, baby, pet, health/OTC)
- Stores in Peoria and East Peoria, IL: Costco, Sam's Club, Walmart, Target,
  Aldi, Kroger
- Weekly flyer deals + everyday shelf prices
- Cross-store unit-price comparison
- Email digest with trend badges and historical context
- CLI tools for on-demand price checks and manual updates

**Out of scope (for now):**
- Produce pricing (prices fluctuate too fast and vary by item quality)
- Online-only pricing or delivery surcharges
- Loyalty card / member pricing (tracked separately where possible)
- Receipt scanning or purchase history
- Budget tracking or spending analysis
