"""
Deal matcher — filters AdItems against the user's brand/category watchlist.
"""

from __future__ import annotations

from typing import Any

import yaml

from src.models import AdItem, MatchedDeal


class BrandMatcher:
    """
    Matches scraped AdItems against conf/brands/watchlist.yml.

    Matching rules (all must pass):
      1. item category (or product name) contains the watchlist category keyword
      2. item brand (or product name) contains one of the acceptable brands
         — skipped if brands list is empty (match any brand)
      3. discount_pct >= min_discount  (if set)
      4. sale_price   <= max_price     (if set)
    """

    def __init__(self, watchlist_path: str = "conf/brands/watchlist.yml"):
        with open(watchlist_path) as f:
            data = yaml.safe_load(f)
        self.categories: dict[str, dict[str, Any]] = data.get("categories", {})

    def match(self, items: list[AdItem]) -> list[MatchedDeal]:
        matches = []
        for item in items:
            result = self._check_item(item)
            if result:
                matches.append(result)

        # sort by priority desc, then discount desc
        matches.sort(key=lambda m: (m.priority, (m.item.discount_pct or 0)), reverse=True)
        return matches

    def _check_item(self, item: AdItem) -> MatchedDeal | None:
        for cat_name, cat_cfg in self.categories.items():
            if not self._category_matches(item, cat_name):
                continue

            brands      = cat_cfg.get("brands", [])
            min_disc    = cat_cfg.get("min_discount")
            max_price   = cat_cfg.get("max_price")
            priority    = cat_cfg.get("priority", 1)

            # brand filter
            matched_brand = None
            if brands:
                matched_brand = self._find_brand(item, brands)
                if not matched_brand:
                    continue
            # discount filter
            if min_disc and (item.discount_pct or 0) < min_disc:
                continue
            # price ceiling
            if max_price and item.sale_price > max_price:
                continue

            return MatchedDeal(
                item             = item,
                matched_category = cat_name,
                matched_brand    = matched_brand,
                priority         = priority,
            )
        return None

    def _category_matches(self, item: AdItem, cat_name: str) -> bool:
        """Check if item belongs to a watchlist category."""
        haystack = " ".join(filter(None, [
            item.category or "",
            item.product_name,
        ])).lower()
        return cat_name.lower() in haystack

    def _find_brand(self, item: AdItem, brands: list[str]) -> str | None:
        """Return the first matching brand string, or None."""
        haystack = " ".join(filter(None, [
            item.brand or "",
            item.product_name,
        ])).lower()
        for brand in brands:
            if brand.lower() in haystack:
                return brand
        return None
