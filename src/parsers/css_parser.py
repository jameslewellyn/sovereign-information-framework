"""
CSS-based parser — no LLM, instant, free.
Used when store HTML structure is stable and CSS selectors are known.
"""

from __future__ import annotations

import re
from typing import Any

from src.models import AdItem


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    return float(match.group()) if match else None


class CSSParser:
    def __init__(self, selectors: dict[str, str], store: str, source: str):
        self.selectors = selectors
        self.store     = store
        self.source    = source

    def parse(self, raw_items: list[dict[str, Any]]) -> list[AdItem]:
        items = []
        for r in raw_items:
            sale  = _parse_price(r.get("sale_price"))
            if not sale:
                continue
            orig     = _parse_price(r.get("original_price"))
            discount = None
            if orig and orig > sale:
                discount = round((orig - sale) / orig * 100, 1)

            items.append(AdItem(
                store          = self.store,
                source         = self.source,
                product_name   = r.get("name", ""),
                sale_price     = sale,
                original_price = orig,
                discount_pct   = discount,
                image_url      = r.get("image_url"),
            ))
        return items
