"""
Core data models for the deals-tracker pipeline.

Key design principle:
  Every price is stored in two forms:
    1. pack_price   — what you actually pay at the register
    2. unit_price   — pack_price / total_base_units
                      (e.g. $/oz, $/roll, $/lb, $/load, $/count)

  Unit price is the ONLY fair cross-store comparison metric.
  $22.99 for 30 rolls vs $18.99 for 18 rolls is meaningless without it.
  ($0.77/roll vs $1.06/roll — Costco wins by 27%)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator, computed_field


# ── Base unit types ──────────────────────────────────────────────────────────
# The canonical unit a category is normalized to for comparison.
# All pack sizes are converted to this before computing unit_price.

BASE_UNITS: dict[str, str] = {
    # weight
    "oz":    "oz",
    "lb":    "oz",       # 1 lb = 16 oz
    "g":     "oz",       # 1 g  = 0.0353 oz
    "kg":    "oz",       # 1 kg = 35.27 oz
    # volume
    "fl oz": "fl oz",
    "ml":    "fl oz",    # 1 ml = 0.0338 fl oz
    "l":     "fl oz",    # 1 l  = 33.81 fl oz
    "gal":   "fl oz",    # 1 gal = 128 fl oz
    # count/discrete units
    "count": "count",
    "ct":    "count",
    "pack":  "count",
    "roll":  "roll",
    "sheet": "sheet",
    "load":  "load",
    "dose":  "dose",
    "pod":   "pod",
    "bag":   "bag",
    "slice": "slice",
    "piece": "piece",
    "each":  "each",
    # area
    "sqft":  "sqft",
}

UNIT_CONVERSIONS: dict[tuple[str, str], float] = {
    # (from_unit, to_base): multiplier
    ("lb",  "oz"):    16.0,
    ("g",   "oz"):    0.035274,
    ("kg",  "oz"):    35.274,
    ("ml",  "fl oz"): 0.033814,
    ("l",   "fl oz"): 33.814,
    ("gal", "fl oz"): 128.0,
    ("ct",  "count"): 1.0,
    ("pack","count"): 1.0,
}


class PackSize(BaseModel):
    """
    Describes the physical size of a product as sold.
    Examples:
      "30-pack of toilet paper rolls"  → quantity=30, unit="roll"
      "64 fl oz bottle of juice"       → quantity=64,  unit="fl oz"
      "2.5 lb bag of coffee"           → quantity=2.5, unit="lb"
      "3-pack of 48oz bottles"         → quantity=3,   unit="pack",
                                          inner_quantity=48, inner_unit="fl oz"
    """
    quantity:       float
    unit:           str                      # roll, oz, fl oz, lb, count, load …
    inner_quantity: Optional[float] = None  # for "3-pack of 48oz" → 48
    inner_unit:     Optional[str]   = None  # for "3-pack of 48oz" → "fl oz"
    raw_description: Optional[str]  = None  # original string from scrape

    @property
    def total_base_units(self) -> float:
        """Total in the canonical base unit for this unit type."""
        if self.inner_quantity and self.inner_unit:
            # multi-level: 3 packs × 48 fl oz each = 144 fl oz
            inner_base = _to_base(self.inner_quantity, self.inner_unit)
            return self.quantity * inner_base
        return _to_base(self.quantity, self.unit)

    @property
    def base_unit(self) -> str:
        if self.inner_unit:
            return BASE_UNITS.get(self.inner_unit.lower(), self.inner_unit)
        return BASE_UNITS.get(self.unit.lower(), self.unit)

    def __str__(self) -> str:
        if self.inner_quantity and self.inner_unit:
            return f"{self.quantity:.4g}-pack × {self.inner_quantity:.4g} {self.inner_unit}"
        return f"{self.quantity:.4g} {self.unit}"


def _to_base(quantity: float, unit: str) -> float:
    unit_lower = unit.lower()
    base = BASE_UNITS.get(unit_lower, unit_lower)
    conv = UNIT_CONVERSIONS.get((unit_lower, base), 1.0)
    return quantity * conv


class PriceObservation(BaseModel):
    """
    A single price data point — the atomic unit of storage.

    Every time we scrape a price (from a flyer, product page, or API)
    we record one of these. They accumulate over time to form history.
    """
    # identity
    id:             Optional[str] = None     # UUID assigned at insert
    product_key:    str                      # e.g. "paper-goods/toilet-paper-bulk"
    canonical_name: str                      # human label from catalog
    category:       str
    subcategory:    Optional[str] = None
    brand:          Optional[str] = None

    # store
    store:          str
    store_location: Optional[str] = None    # city / zip code
    chain:          Optional[str] = None    # e.g. "Walmart" even if store="Walmart Supercenter"

    # pack info
    pack_size:      Optional[PackSize] = None
    product_name:   str                     # as listed in the ad/page

    # pricing
    pack_price:     float                   # what you pay at register
    unit_price:     Optional[float] = None  # pack_price / total_base_units
    base_unit:      Optional[str]   = None  # the unit unit_price is per (oz, roll, load…)
    price_type:     Literal["sale", "everyday"] = "everyday"

    # sale metadata (only for price_type="sale")
    original_pack_price:  Optional[float] = None
    original_unit_price:  Optional[float] = None
    discount_pct:         Optional[float] = None
    valid_from:           Optional[date]  = None
    valid_until:          Optional[date]  = None

    # provenance
    source:         str                     # adapter name
    source_url:     Optional[str] = None
    observed_at:    datetime = Field(default_factory=datetime.utcnow)
    flyer_id:       Optional[str] = None    # for deduplication across runs

    @model_validator(mode="after")
    def compute_unit_price(self) -> "PriceObservation":
        """Auto-compute unit_price if pack_size is provided."""
        if self.pack_size and self.unit_price is None:
            total = self.pack_size.total_base_units
            if total > 0:
                self.unit_price   = round(self.pack_price / total, 4)
                self.base_unit    = self.pack_size.base_unit
                if self.original_pack_price:
                    self.original_unit_price = round(
                        self.original_pack_price / total, 4
                    )
        return self

    @computed_field
    @property
    def sale_status(self) -> Literal["everyday", "upcoming", "active", "expired"]:
        """
        Classifies this price observation by its temporal sale state.

        everyday — standard shelf price, no promotional window
        upcoming — sale is in a flyer but hasn't started yet
        active   — sale is running right now
        expired  — sale window has passed (kept for history)
        """
        if self.price_type == "everyday":
            return "everyday"
        today = datetime.now(timezone.utc).date()
        if self.valid_from and self.valid_from > today:
            return "upcoming"
        if self.valid_until and self.valid_until < today:
            return "expired"
        return "active"


class PriceStats(BaseModel):
    """
    Aggregated statistics for a (product_key, store) pair.
    Recomputed after each new observation is saved.
    Enables trend detection without re-scanning all history.
    """
    product_key:        str
    store:              str
    base_unit:          Optional[str]

    # current
    current_pack_price:  float
    current_unit_price:  Optional[float]
    last_observed_at:    datetime

    # 30-day stats
    avg_unit_price_30d:  Optional[float]
    min_unit_price_30d:  Optional[float]
    max_unit_price_30d:  Optional[float]

    # 90-day stats
    avg_unit_price_90d:  Optional[float]
    min_unit_price_90d:  Optional[float]
    max_unit_price_90d:  Optional[float]
    observation_count_90d: int = 0

    # derived signals
    vs_30d_avg_pct:  Optional[float] = None   # % above/below 30d average
    is_all_time_low: bool = False
    price_trend:     Optional[Literal["rising", "falling", "stable"]] = None


class UpcomingSale(BaseModel):
    """
    A sale that has been scraped from a flyer but has not started yet.

    Flyers are typically distributed 3–7 days before the sale begins.
    Recording these prevents buying at today's full price when a better
    price is days away.
    """
    product_key:    str
    canonical_name: str
    category:       str
    store:          str
    brand:          Optional[str] = None

    sale_pack_price:  float
    sale_unit_price:  Optional[float] = None
    base_unit:        Optional[str]   = None
    pack_description: Optional[str]   = None
    original_pack_price: Optional[float] = None
    discount_pct:     Optional[float] = None

    valid_from:   date
    valid_until:  Optional[date] = None
    days_until:   int              # calendar days until sale starts

    source_url:   Optional[str] = None


class WaitSignal(BaseModel):
    """
    A 'don't buy yet' recommendation generated when an upcoming sale
    price is meaningfully better than what you'd pay today.

    Shown prominently in the digest and price_check output.
    """
    product_key:    str
    canonical_name: str

    # what you'd pay if you bought today
    today_store:      str
    today_unit_price: float
    today_pack_price: float
    base_unit:        Optional[str] = None

    # what's coming
    upcoming:         UpcomingSale

    # how much better the upcoming price is
    unit_savings:     float       # today_unit_price - upcoming sale unit price
    pct_savings:      float       # unit_savings / today_unit_price * 100


class ComparisonRow(BaseModel):
    """One row in the cross-store comparison table."""
    product_key:    str
    category:       str
    canonical_name: str
    base_unit:      Optional[str]

    # store → latest unit price
    unit_prices:    dict[str, float]
    # store → latest pack price + pack description
    pack_info:      dict[str, str]    # store → "30 rolls @ $22.99"

    best_store:     str
    best_unit_price:  float
    worst_unit_price: float
    unit_savings:     float           # worst - best unit price
    pct_savings:      float           # (worst-best)/worst * 100

    stats:          dict[str, PriceStats] = {}   # store → stats (for trend badges)
    wait_signals:   list[WaitSignal]      = []   # upcoming sales better than current


class AdItem(BaseModel):
    """
    Lightweight transfer object from adapters → pipeline.
    Adapters return these; the pipeline converts to PriceObservation for storage.
    Kept for backwards compatibility with existing adapter code.
    """
    store:          str
    source:         str
    product_name:   str
    brand:          Optional[str]  = None
    category:       Optional[str]  = None
    sale_price:     float
    original_price: Optional[float] = None
    discount_pct:   Optional[float] = None
    unit:           Optional[str]   = None
    price_type:     Literal["sale", "everyday"] = "sale"
    valid_from:     Optional[date]  = None
    valid_until:    Optional[date]  = None
    image_url:      Optional[str]   = None
    source_url:     Optional[str]   = None
    scraped_at:     datetime = Field(default_factory=datetime.utcnow)


class MatchedDeal(BaseModel):
    """An AdItem matched against the user's brand/category watchlist."""
    item:             AdItem
    observation:      Optional[PriceObservation] = None  # enriched form
    stats:            Optional[PriceStats] = None        # historical context
    matched_category: str
    matched_brand:    Optional[str] = None
    priority:         int = 0
    vs_avg_label:     Optional[str] = None   # e.g. "31% below 90d avg"


class DigestEntry(BaseModel):
    """One line item in the email digest."""
    category:            str
    brand:               Optional[str]
    product_name:        str
    store:               str
    pack_price:          float
    unit_price:          Optional[float]
    base_unit:           Optional[str]
    pack_description:    Optional[str]    # "30 rolls"
    original_pack_price: Optional[float]
    discount_pct:        Optional[float]
    valid_from:          Optional[date] = None
    valid_until:         Optional[date] = None
    sale_status:         Literal["everyday", "upcoming", "active", "expired"] = "active"
    source_url:          Optional[str]
    price_type:          Literal["sale", "everyday"] = "sale"
    vs_avg_label:        Optional[str] = None
    is_all_time_low:     bool = False


class Digest(BaseModel):
    """Full email digest payload."""
    generated_at:    datetime = Field(default_factory=datetime.utcnow)
    entries:         list[DigestEntry]           # active deals now
    upcoming:        list[DigestEntry] = []      # sales starting in future
    wait_signals:    list[WaitSignal]  = []      # don't-buy-yet alerts
    comparisons:     list[ComparisonRow] = []
    total_deals:     int
    stores_covered:  list[str]
