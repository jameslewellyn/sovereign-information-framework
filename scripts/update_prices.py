#!/usr/bin/env python3
"""
update_prices — scrape current prices then immediately show the database result.

Runs the product-page adapter (everyday prices) and/or the Flipp adapter
(sale flyers) for all configured stores, saves to the database, then calls
price_check to display what was captured.

Usage:
  python scripts/update_prices.py                      # all catalog products
  python scripts/update_prices.py milk                 # products matching "milk"
  python scripts/update_prices.py --adapter flipp      # flyer deals only
  python scripts/update_prices.py --adapter pages      # product pages only
  python scripts/update_prices.py --dry-run            # show plan, no DB writes
  python scripts/update_prices.py milk --no-check      # update without price_check
  python scripts/update_prices.py --all --adapter both # full run

Run from the deals-tracker root directory.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from src.adapters.flipp        import FlippAdapter
from src.adapters.product_page import ProductPageAdapter
from src.models import AdItem, PriceObservation
from src.storage.db import Database
from src.storage.unit_normalizer import enrich_with_unit_price


# ── ANSI helpers (same as price_check) ───────────────────────────────────────

USE_COLOUR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOUR else text

GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33",   t)
RED    = lambda t: _c("31",   t)
CYAN   = lambda t: _c("36",   t)
BOLD   = lambda t: _c("1",    t)
DIM    = lambda t: _c("2",    t)


# ── Config loading ────────────────────────────────────────────────────────────

def _load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}

def _load_adapter_configs(conf_dir: Path) -> list[dict]:
    configs = []
    adapter_dir = conf_dir / "adapters"
    if not adapter_dir.exists():
        return configs
    for yml in sorted(adapter_dir.glob("*.yml")):
        if yml.stem.endswith(".example"):
            continue
        cfg = _load_yaml(yml)
        if cfg:
            cfg["_source_file"] = yml.name
            configs.append(cfg)
    return configs


# ── Product page update ───────────────────────────────────────────────────────

async def run_product_pages(
    db: Database,
    catalog: dict,
    query: str | None,
    dry_run: bool,
) -> int:
    """Scrape everyday prices from product pages in the catalog."""
    # filter catalog to matching products if query given
    filtered = _filter_catalog(catalog, query)
    if not filtered.get("categories"):
        print(YELLOW("  No catalog products matched the query."))
        return 0

    count_urls = sum(
        len(prod.get("stores", {}))
        for cat in filtered["categories"].values()
        for prod in cat.get("products", {}).values()
    )
    print(f"  {CYAN(str(count_urls))} product-page URLs to scrape ...")

    if dry_run:
        for cat_slug, cat in filtered["categories"].items():
            for prod_slug, prod in cat.get("products", {}).items():
                for store, url in prod.get("stores", {}).items():
                    print(f"  {DIM('would scrape')} {store:20s}  {prod['label']}  {DIM(url[:60])}")
        return 0

    adapter = ProductPageAdapter(config={"catalog": filtered, "llm_model": _pick_llm()})
    t0    = time.time()
    items = await adapter.fetch()
    elapsed = time.time() - t0

    print(f"  fetched {CYAN(str(len(items)))} prices in {elapsed:.1f}s")

    saved = _save_items_to_db(db, items, filtered)
    print(f"  {GREEN(str(saved))} observations saved to database")
    return saved


def _pick_llm() -> str:
    """Use env var if set, otherwise fall back to free local Ollama."""
    import os
    return os.environ.get("LLM_MODEL", "ollama/llama3.2")


def _filter_catalog(catalog: dict, query: str | None) -> dict:
    if not query:
        return catalog
    q = query.lower()
    filtered: dict = {"categories": {}}
    for cat_slug, cat in catalog.get("categories", {}).items():
        prods = {}
        for prod_slug, prod in cat.get("products", {}).items():
            if (
                q in prod_slug.lower()
                or q in prod.get("label", "").lower()
                or q in cat.get("label", "").lower()
                or q in cat_slug.lower()
            ):
                prods[prod_slug] = prod
        if prods:
            filtered["categories"][cat_slug] = {**cat, "products": prods}
    return filtered


def _save_items_to_db(db: Database, items: list[AdItem], catalog: dict) -> int:
    """Convert AdItems → PriceObservations using catalog metadata, then save."""
    # build lookup: (store, product_label) → (product_key, base_unit)
    lookup: dict[tuple[str, str], tuple[str, str, str]] = {}
    for cat_slug, cat in catalog.get("categories", {}).items():
        cat_label = cat.get("label", cat_slug)
        for prod_slug, prod in cat.get("products", {}).items():
            product_key = f"{cat_slug}/{prod_slug}"
            for store in prod.get("stores", {}):
                lookup[(store.lower(), prod["label"].lower())] = (
                    product_key, cat_label, prod.get("unit", "")
                )

    observations: list[PriceObservation] = []
    for item in items:
        key = (item.store.lower(), item.product_name.lower())
        # try exact match first, then prefix
        meta = lookup.get(key)
        if not meta:
            for (s, p), m in lookup.items():
                if s == item.store.lower() and (p in item.product_name.lower() or item.product_name.lower() in p):
                    meta = m
                    break

        product_key = meta[0] if meta else f"unknown/{item.product_name[:30].lower().replace(' ', '-')}"
        category    = meta[1] if meta else (item.category or "Unknown")
        catalog_unit = meta[2] if meta else None

        unit_price, base_unit, pack_size = enrich_with_unit_price(
            item.sale_price, item.unit, catalog_unit
        )

        observations.append(PriceObservation(
            product_key    = product_key,
            canonical_name = item.product_name,
            category       = category,
            brand          = item.brand,
            store          = item.store,
            product_name   = item.product_name,
            pack_size      = pack_size,
            pack_price     = item.sale_price,
            unit_price     = unit_price,
            base_unit      = base_unit,
            price_type     = item.price_type,
            original_pack_price = item.original_price,
            discount_pct   = item.discount_pct,
            valid_from     = item.valid_from,
            valid_until    = item.valid_until,
            source         = item.source,
            source_url     = item.source_url,
            observed_at    = item.scraped_at,
        ))

    if observations:
        db.save_observations(observations)

    return len(observations)


# ── Flipp flyer update ────────────────────────────────────────────────────────

async def run_flipp(
    db: Database,
    adapter_configs: list[dict],
    query: str | None,
    dry_run: bool,
) -> int:
    flipp_configs = [c for c in adapter_configs if c.get("adapter") == "flipp" and c.get("enabled", True)]
    if not flipp_configs:
        print(YELLOW("  No enabled Flipp adapters found in conf/adapters/"))
        return 0

    print(f"  {CYAN(str(len(flipp_configs)))} Flipp adapter(s): "
          f"{', '.join(c.get('_source_file', '?') for c in flipp_configs)}")

    if dry_run:
        for cfg in flipp_configs:
            stores = ", ".join(cfg.get("stores") or ["all stores"])
            print(f"  {DIM('would run')} {cfg['_source_file']}  postal={cfg.get('postal_code')}  stores={stores}")
        return 0

    total_saved = 0
    for cfg in flipp_configs:
        adapter = FlippAdapter(config=cfg)
        t0 = time.time()
        try:
            items = await adapter.fetch()
        except Exception as e:
            print(RED(f"  [{cfg['_source_file']}] fetch failed: {e}"))
            continue

        elapsed = time.time() - t0

        # filter to query if given
        if query:
            q = query.lower()
            items = [i for i in items if q in i.product_name.lower()
                     or (i.category and q in i.category.lower())
                     or (i.brand and q in i.brand.lower())]

        print(f"  [{cfg['_source_file']}] {CYAN(str(len(items)))} items in {elapsed:.1f}s")

        # save flyer items with minimal catalog context
        catalog_stub: dict = {"categories": {}}
        saved = _save_items_to_db(db, items, catalog_stub)
        total_saved += saved

    print(f"  {GREEN(str(total_saved))} total flyer observations saved")
    return total_saved


# ── Main ──────────────────────────────────────────────────────────────────────

async def _run(args):
    conf_dir    = Path("conf")
    catalog     = _load_yaml(conf_dir / "products" / "catalog.yml")
    adapter_cfgs = _load_adapter_configs(conf_dir)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(db_path))

    query = args.query if hasattr(args, "query") else None

    run_pages = args.adapter in ("pages", "both")
    run_flyer = args.adapter in ("flipp", "both")

    total = 0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{BOLD('Update Prices')} — {ts}  db: {DIM(str(db_path))}")
    print(DIM("─" * 70))

    if run_pages:
        print(f"\n{BOLD('→ Product Pages')} (everyday prices)")
        if not catalog.get("categories"):
            print(YELLOW("  conf/products/catalog.yml not found or empty."))
        else:
            total += await run_product_pages(db, catalog, query, args.dry_run)

    if run_flyer:
        print(f"\n{BOLD('→ Flipp Flyers')} (sale prices)")
        total += await run_flipp(db, adapter_cfgs, query, args.dry_run)

    db.close()

    print(DIM("\n" + "─" * 35))
    if args.dry_run:
        print(YELLOW("Dry run complete — no data written."))
    else:
        print(f"{GREEN('Done.')} {total} observations written.")

    # ── run price_check automatically unless suppressed ───────────────────────
    if not args.no_check and not args.dry_run and total > 0:
        print(f"\n{BOLD('Current Database State:')}")
        import subprocess
        check_args = ["python", "scripts/price_check.py"]
        if query:
            check_args.append(query)
        else:
            check_args.append("--all")
        subprocess.run(check_args)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape current prices and save to database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/update_prices.py                  # pages + flipp, all products
  python scripts/update_prices.py milk             # only products matching "milk"
  python scripts/update_prices.py --adapter flipp  # flyer sales only
  python scripts/update_prices.py --adapter pages  # everyday prices only
  python scripts/update_prices.py --dry-run        # show plan, no writes
  python scripts/update_prices.py milk --no-check  # update without showing results
        """,
    )
    parser.add_argument("query",       nargs="?",          help="Product search filter")
    parser.add_argument("--adapter",   "-a",
                        choices=["pages", "flipp", "both"], default="both",
                        help="Which adapter to run (default: both)")
    parser.add_argument("--dry-run",   action="store_true", help="Show plan without writing")
    parser.add_argument("--no-check",  action="store_true", help="Skip price_check after update")
    parser.add_argument("--db",        default="data/deals.duckdb", help="Path to DuckDB file")
    args = parser.parse_args()

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
