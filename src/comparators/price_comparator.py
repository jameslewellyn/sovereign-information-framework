"""
PriceComparator — cross-store unit price comparison with trend insights.

All comparisons are on unit_price (e.g. $/roll, $/oz) so different
pack sizes are fairly compared. Raw pack prices are shown alongside
for context.

Also detects:
  - Shrinkflation: same price, smaller pack → higher unit price over time
  - Fake sales: unit price hasn't actually dropped vs recent history
  - Best-time-to-buy: item is near its historical low
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from src.models import ComparisonRow, PriceStats, UpcomingSale, WaitSignal
from src.storage.db import Database


# Minimum unit-price saving (%) for a WaitSignal to be worth showing.
# Below this threshold the saving is noise — not worth changing your plans.
WAIT_SIGNAL_MIN_PCT = 8.0

# Trend badge strings shown in the email
TREND_BADGES = {
    "rising":  "↑ rising",
    "falling": "↓ falling",
    "stable":  "→ stable",
}


class PriceComparator:

    def __init__(self, db: Database):
        self.db = db

    def compare_all(self) -> list[ComparisonRow]:
        product_keys = self._get_tracked_product_keys()
        rows = []
        for key in product_keys:
            row = self._compare_product(key)
            if row:
                rows.append(row)
        rows.sort(key=lambda r: r.unit_savings, reverse=True)
        return rows

    def compare_category(self, category: str) -> list[ComparisonRow]:
        product_keys = self._get_tracked_product_keys(category=category)
        rows = [self._compare_product(k) for k in product_keys]
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: r.unit_savings, reverse=True)
        return rows

    def _compare_product(self, product_key: str) -> Optional[ComparisonRow]:
        store_rows = self.db.get_latest_per_store(product_key)
        # only compare stores that have unit prices
        priced = [r for r in store_rows if r["unit_price"] is not None]
        if len(priced) < 2:
            return None

        unit_prices = {r["store"]: r["unit_price"] for r in priced}
        pack_info   = {
            r["store"]: f"{r['pack_description'] or '?'} @ ${r['pack_price']:.2f}"
            for r in priced
        }

        best_store       = min(unit_prices, key=unit_prices.__getitem__)
        worst_store      = max(unit_prices, key=unit_prices.__getitem__)
        best_unit_price  = unit_prices[best_store]
        worst_unit_price = unit_prices[worst_store]
        unit_savings     = round(worst_unit_price - best_unit_price, 4)
        pct_savings      = round(unit_savings / worst_unit_price * 100, 1) if worst_unit_price else 0

        # pull stats for trend badges
        stats = {}
        for r in priced:
            s = self.db.get_stats(product_key, r["store"])
            if s:
                stats[r["store"]] = s

        return ComparisonRow(
            product_key     = product_key,
            category        = priced[0].get("store", ""),   # overridden by caller
            canonical_name  = priced[0]["product_name"],
            base_unit       = priced[0]["base_unit"],
            unit_prices     = unit_prices,
            pack_info       = pack_info,
            best_store      = best_store,
            best_unit_price = best_unit_price,
            worst_unit_price= worst_unit_price,
            unit_savings    = unit_savings,
            pct_savings     = pct_savings,
            stats           = stats,
        )

    def detect_shrinkflation(self, product_key: str, store: str, days: int = 180) -> list[dict]:
        """
        Detect if a store has kept price flat but reduced pack size,
        causing unit price to creep up without an obvious price hike.
        Returns list of events: {date, old_pack, new_pack, unit_price_change_pct}
        """
        history = self.db.get_price_history(product_key, store, days=days)
        if len(history) < 2:
            return []

        events = []
        for i in range(len(history) - 1):
            curr = history[i]
            prev = history[i + 1]
            # same or close pack price but unit price went up
            price_similar = abs(curr["pack_price"] - prev["pack_price"]) < 0.50
            if (
                price_similar
                and curr["unit_price"] and prev["unit_price"]
                and curr["unit_price"] > prev["unit_price"] * 1.05  # >5% unit price increase
            ):
                pct_change = round(
                    (curr["unit_price"] - prev["unit_price"]) / prev["unit_price"] * 100, 1
                )
                events.append({
                    "date":           curr["observed_at"],
                    "old_pack":       prev["pack_description"],
                    "new_pack":       curr["pack_description"],
                    "pack_price":     curr["pack_price"],
                    "old_unit_price": prev["unit_price"],
                    "new_unit_price": curr["unit_price"],
                    "unit_price_increase_pct": pct_change,
                })
        return events

    def detect_fake_sale(self, product_key: str, store: str) -> bool:
        """
        A 'fake sale' is when an item is marked on sale but its current
        unit_price is >= its 30-day average (price was raised beforehand).
        """
        stats = self.db.get_stats(product_key, store)
        if not stats or not stats.current_unit_price or not stats.avg_unit_price_30d:
            return False
        # sale is only "real" if current unit price is meaningfully below avg
        return stats.current_unit_price >= stats.avg_unit_price_30d * 0.97

    def vs_avg_label(self, stats: PriceStats) -> Optional[str]:
        """Human-readable label for how current price compares to history."""
        if not stats or stats.vs_30d_avg_pct is None:
            return None
        pct = stats.vs_30d_avg_pct
        if pct <= -20:
            return f"🔥 {abs(pct):.0f}% below 30d avg"
        if pct <= -10:
            return f"✅ {abs(pct):.0f}% below 30d avg"
        if pct >= 15:
            return f"⚠️ {pct:.0f}% above 30d avg"
        return None

    # ── Wait signals ──────────────────────────────────────────────────────

    def build_wait_signals(self, days_ahead: int = 14) -> list[WaitSignal]:
        """
        Generate WaitSignal objects for every upcoming sale that is
        meaningfully cheaper than what you'd pay today.

        Logic:
          For each upcoming sale observation (valid_from > today):
            - Find the current everyday price for the same product at any store.
            - If the upcoming unit price is >= WAIT_SIGNAL_MIN_PCT cheaper,
              emit a WaitSignal pointing at the cheapest store to buy from today
              so the user knows the comparison is fair.
        """
        upcoming_rows = self.db.get_upcoming_sales(days_ahead=days_ahead)
        if not upcoming_rows:
            return []

        # index upcoming by product_key
        upcoming_by_product: dict[str, list[dict]] = defaultdict(list)
        for r in upcoming_rows:
            upcoming_by_product[r["product_key"]].append(r)

        # fetch current everyday prices for those products only
        product_keys = list(upcoming_by_product.keys())
        everyday_rows = self.db.get_current_everyday_prices(product_keys=product_keys)

        # index everyday prices by product_key → list of (store, unit_price, pack_price, ...)
        everyday_by_product: dict[str, list[dict]] = defaultdict(list)
        for r in everyday_rows:
            if r["unit_price"] is not None:
                everyday_by_product[r["product_key"]].append(r)

        today = datetime.now(timezone.utc).date()
        signals: list[WaitSignal] = []

        for product_key, up_rows in upcoming_by_product.items():
            everyday = everyday_by_product.get(product_key, [])
            if not everyday:
                continue

            # cheapest store to buy from today
            best_today = min(everyday, key=lambda r: r["unit_price"])

            for up in up_rows:
                if up["unit_price"] is None:
                    continue

                today_up  = best_today["unit_price"]
                sale_up   = up["unit_price"]
                saving    = today_up - sale_up
                pct       = saving / today_up * 100 if today_up else 0

                if pct < WAIT_SIGNAL_MIN_PCT:
                    continue

                days_until = (up["valid_from"] - today).days

                signals.append(WaitSignal(
                    product_key     = product_key,
                    canonical_name  = up["canonical_name"] or product_key,
                    today_store     = best_today["store"],
                    today_unit_price = today_up,
                    today_pack_price = best_today["pack_price"],
                    base_unit       = up["base_unit"] or best_today.get("base_unit"),
                    upcoming        = UpcomingSale(
                        product_key      = product_key,
                        canonical_name   = up["canonical_name"] or product_key,
                        category         = up["category"] or "",
                        store            = up["store"],
                        brand            = up["brand"],
                        sale_pack_price  = up["pack_price"],
                        sale_unit_price  = sale_up,
                        base_unit        = up["base_unit"],
                        pack_description = up["pack_description"],
                        original_pack_price = up["original_pack_price"],
                        discount_pct     = up["discount_pct"],
                        valid_from       = up["valid_from"],
                        valid_until      = up["valid_until"],
                        days_until       = days_until,
                        source_url       = up["source_url"],
                    ),
                    unit_savings = round(saving, 4),
                    pct_savings  = round(pct, 1),
                ))

        # sort: biggest savings first
        signals.sort(key=lambda s: s.pct_savings, reverse=True)
        return signals

    def get_upcoming_sales(self, days_ahead: int = 14) -> list[UpcomingSale]:
        """Return all upcoming sales as UpcomingSale objects."""
        today = datetime.now(timezone.utc).date()
        rows  = self.db.get_upcoming_sales(days_ahead=days_ahead)
        result = []
        for r in rows:
            result.append(UpcomingSale(
                product_key      = r["product_key"],
                canonical_name   = r["canonical_name"] or r["product_key"],
                category         = r["category"] or "",
                store            = r["store"],
                brand            = r["brand"],
                sale_pack_price  = r["pack_price"],
                sale_unit_price  = r["unit_price"],
                base_unit        = r["base_unit"],
                pack_description = r["pack_description"],
                original_pack_price = r["original_pack_price"],
                discount_pct     = r["discount_pct"],
                valid_from       = r["valid_from"],
                valid_until      = r["valid_until"],
                days_until       = (r["valid_from"] - today).days,
                source_url       = r["source_url"],
            ))
        return result

    def _get_tracked_product_keys(self, category: Optional[str] = None) -> list[str]:
        where = ""
        params = []
        if category:
            where = "WHERE category ILIKE ?"
            params = [f"%{category}%"]
        rows = self.db.conn.execute(
            f"SELECT DISTINCT product_key FROM price_observations {where} ORDER BY product_key",
            params,
        ).fetchall()
        return [r[0] for r in rows]
