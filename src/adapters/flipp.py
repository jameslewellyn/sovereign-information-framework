"""
Flipp API adapter.
Covers: Walmart, Safeway, No Frills, Real Canadian Superstore,
        Save-On-Foods, Loblaws, Target, Kroger, Walgreens, and more.

No authentication required — uses Flipp's public undocumented API.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from src.adapters.base import SourceAdapter
from src.models import AdItem


class FlippAdapter(SourceAdapter):
    name = "flipp"

    SEARCH_URL = "https://backflipp.wishabi.com/flipp/items/search"

    async def fetch(self) -> list[AdItem]:
        postal_code = self.config["postal_code"]
        stores      = self.config.get("stores") or [None]
        locale      = self.config.get("locale", "en")

        items: list[AdItem] = []

        async with httpx.AsyncClient(timeout=30) as client:
            tasks = [
                self._fetch_store(client, postal_code, locale, store)
                for store in stores
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                print(f"[flipp] fetch error: {result}")
                continue
            items.extend(result)

        # deduplicate by (store, product_name, sale_price)
        seen = set()
        unique = []
        for item in items:
            key = (item.store, item.product_name, item.sale_price)
            if key not in seen:
                seen.add(key)
                unique.append(item)

        return unique

    async def _fetch_store(
        self,
        client: httpx.AsyncClient,
        postal_code: str,
        locale: str,
        store: str | None,
    ) -> list[AdItem]:
        params: dict[str, Any] = {"locale": locale, "postal_code": postal_code}
        if store:
            params["q"] = store

        resp = await client.get(self.SEARCH_URL, params=params)
        resp.raise_for_status()
        raw_items = resp.json().get("items", [])

        return [self._normalise(r) for r in raw_items if r.get("current_price")]

    def _normalise(self, raw: dict) -> AdItem:
        original = raw.get("pre_price") or raw.get("regular_price")
        sale     = raw["current_price"]
        discount = None
        if original and original > sale:
            discount = round((original - sale) / original * 100, 1)

        valid_until = None
        if raw.get("valid_to"):
            try:
                valid_until = date.fromisoformat(raw["valid_to"][:10])
            except ValueError:
                pass

        return AdItem(
            store        = raw.get("retailer_name") or raw.get("merchant", "Unknown"),
            source       = self.name,
            product_name = raw.get("name", ""),
            brand        = raw.get("brand"),
            category     = raw.get("category"),
            sale_price   = float(sale),
            original_price = float(original) if original else None,
            discount_pct = discount,
            unit         = raw.get("display_name"),
            valid_until  = valid_until,
            image_url    = raw.get("image_url"),
            source_url   = raw.get("flyer_item_url"),
        )
