"""
Unit normalizer — parses free-text pack descriptions into structured PackSize.

Handles all the messy real-world strings scraped from store pages:
  "30 Double Rolls"           → PackSize(quantity=30, unit="roll")
  "2-pack, 64 fl oz each"     → PackSize(quantity=2, unit="pack", inner_quantity=64, inner_unit="fl oz")
  "2.5 lbs"                   → PackSize(quantity=2.5, unit="lb")
  "146 loads"                 → PackSize(quantity=146, unit="load")
  "48 oz / 3 lb"              → PackSize(quantity=48, unit="oz")
  "4.1 oz, 4-pack"            → PackSize(quantity=4, unit="pack", inner_quantity=4.1, inner_unit="oz")
  "16 Count"                  → PackSize(quantity=16, unit="count")
"""

from __future__ import annotations

import re
from typing import Optional

from src.models import PackSize


# ── Token patterns ────────────────────────────────────────────────────────────

_NUM  = r"(\d+(?:\.\d+)?)"                   # integer or decimal
_SEP  = r"[\s\-×x*,./]+"                     # separators between tokens

# unit aliases → canonical unit
UNIT_ALIASES: dict[str, str] = {
    # rolls / sheets
    "rolls":   "roll",  "roll":    "roll",
    "double roll": "roll", "triple roll": "roll",
    "mega roll": "roll", "giant roll": "roll",
    "sheets":  "sheet", "sheet":  "sheet",
    # counts
    "count":   "count", "ct":     "count", "pk":    "count",
    "pack":    "count", "packs":  "count", "pieces": "count",
    "piece":   "count", "tabs":   "count", "tablets": "count",
    "capsules":"count", "caps":   "count", "pods":  "count",
    "pod":     "pod",   "wipes":  "count",
    # weight
    "oz":      "oz",    "ounce":  "oz",    "ounces": "oz",
    "fl oz":   "fl oz", "fluid oz": "fl oz", "fluid ounce": "fl oz",
    "lb":      "lb",    "lbs":    "lb",    "pound":  "lb",    "pounds": "lb",
    "g":       "g",     "gram":   "g",     "grams":  "g",
    "kg":      "kg",    "kilogram": "kg",  "kilograms": "kg",
    # volume
    "ml":      "ml",    "milliliter": "ml", "milliliters": "ml",
    "l":       "l",     "liter":  "l",     "liters": "l",
    "gal":     "gal",   "gallon": "gal",   "gallons": "gal",
    "qt":      "fl oz",  # 1 qt = 32 fl oz — convert at parse time
    "quart":   "fl oz",
    "pt":      "fl oz",  # 1 pt = 16 fl oz
    "pint":    "fl oz",
    # loads/doses
    "loads":   "load",  "load":   "load",
    "doses":   "dose",  "dose":   "dose",
    "uses":    "dose",
    # other
    "bags":    "bag",   "bag":    "bag",
    "slices":  "slice", "slice":  "slice",
    "each":    "each",
    "sqft":    "sqft",  "sq ft":  "sqft",
    "sq. ft.": "sqft",
}

# quantities with irregular multipliers when used as descriptors
DESCRIPTOR_MULTIPLIERS: dict[str, float] = {
    "double": 2.0,
    "triple": 3.0,
    "mega":   4.0,   # approximate
    "giant":  5.0,   # approximate
    "super":  2.5,
    "jumbo":  3.0,
}

QT_TO_FLOZ = 32.0
PT_TO_FLOZ = 16.0


def parse_pack_size(text: str) -> Optional[PackSize]:
    """
    Parse a free-text pack description into a PackSize.
    Returns None if no structured size can be extracted.
    """
    if not text:
        return None
    raw = text.strip()
    text_lower = raw.lower()

    # ── Pattern 1: "N-pack of X unit" or "N × X unit" ────────────────────
    # e.g. "3-pack, 48 fl oz each", "4 pack × 4.1 oz"
    m = re.search(
        rf"{_NUM}\s*[-×x*]?\s*(?:pack|pk|count|ct){_SEP}{_NUM}\s*([a-z \.]+)",
        text_lower,
    )
    if m:
        outer_qty  = float(m.group(1))
        inner_qty  = float(m.group(2))
        inner_unit = _resolve_unit(m.group(3).strip())
        if inner_unit:
            return PackSize(
                quantity=outer_qty, unit="pack",
                inner_quantity=inner_qty, inner_unit=inner_unit,
                raw_description=raw,
            )

    # ── Pattern 2: "X unit, N-pack" (reversed) ───────────────────────────
    # e.g. "4.1 oz, 4-pack", "48 fl oz, 2 count"
    m = re.search(
        rf"{_NUM}\s*([a-z \.]+?)\s*,\s*{_NUM}\s*[-]?\s*(?:pack|pk|count|ct)",
        text_lower,
    )
    if m:
        inner_qty  = float(m.group(1))
        inner_unit = _resolve_unit(m.group(2).strip())
        outer_qty  = float(m.group(3))
        if inner_unit and outer_qty > 1:
            return PackSize(
                quantity=outer_qty, unit="pack",
                inner_quantity=inner_qty, inner_unit=inner_unit,
                raw_description=raw,
            )

    # ── Pattern 3: descriptor + quantity + unit ───────────────────────────
    # e.g. "30 Double Rolls", "12 Mega Rolls"
    for desc, mult in DESCRIPTOR_MULTIPLIERS.items():
        m = re.search(rf"{_NUM}\s+{desc}\s+([a-z]+)", text_lower)
        if m:
            qty  = float(m.group(1)) * mult
            unit = _resolve_unit(m.group(2))
            if unit:
                return PackSize(quantity=qty, unit=unit, raw_description=raw)

    # ── Pattern 4: simple "N unit" ────────────────────────────────────────
    # e.g. "30 rolls", "2.5 lbs", "146 loads", "64 fl oz"
    m = re.search(rf"{_NUM}\s*(fl oz|sq\.?\s*ft\.?|[a-z]+)", text_lower)
    if m:
        qty  = float(m.group(1))
        unit = _resolve_unit(m.group(2).strip())
        if unit:
            # special volume conversions
            if unit == "qt":
                return PackSize(quantity=qty * QT_TO_FLOZ, unit="fl oz", raw_description=raw)
            if unit == "pt":
                return PackSize(quantity=qty * PT_TO_FLOZ, unit="fl oz", raw_description=raw)
            return PackSize(quantity=qty, unit=unit, raw_description=raw)

    return None


def _resolve_unit(text: str) -> Optional[str]:
    """Map a unit string to its canonical form."""
    clean = text.strip().lower().rstrip("s")   # naive depluralize
    # try exact first
    if text.strip().lower() in UNIT_ALIASES:
        return UNIT_ALIASES[text.strip().lower()]
    if clean in UNIT_ALIASES:
        return UNIT_ALIASES[clean]
    # partial match fallback
    for alias, canonical in UNIT_ALIASES.items():
        if alias in text.strip().lower():
            return canonical
    return None


def enrich_with_unit_price(
    pack_price: float,
    pack_description: Optional[str],
    fallback_unit: Optional[str] = None,
) -> tuple[Optional[float], Optional[str], Optional[PackSize]]:
    """
    Given a pack price and free-text size description, return:
      (unit_price, base_unit, PackSize)

    Returns (None, None, None) if pack cannot be parsed.
    """
    pack = parse_pack_size(pack_description or "")
    if not pack and fallback_unit:
        # try the fallback unit string (from catalog config)
        pack = parse_pack_size(fallback_unit)

    if not pack:
        return None, None, None

    total = pack.total_base_units
    if total <= 0:
        return None, None, None

    unit_price = round(pack_price / total, 4)
    return unit_price, pack.base_unit, pack
