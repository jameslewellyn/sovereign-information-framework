"""
Scheduler — loads all enabled adapters, runs them on their cron schedules,
matches results, persists to DB, and fires the email notifier.

Run with:
    python -m src.scheduler

Or via Docker (see docker-compose.yml).
"""

from __future__ import annotations

import asyncio
import glob
import os

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from src.adapters import load_adapter
from src.matchers.brand_matcher import BrandMatcher
from src.comparators.price_comparator import PriceComparator
from src.notifiers.email_notifier import build_digest, send_digest
from src.storage.db import Database

load_dotenv()

ADAPTER_CONF_DIR  = os.environ.get("ADAPTER_CONF_DIR",  "conf/adapters")
WATCHLIST_PATH    = os.environ.get("WATCHLIST_PATH",     "conf/brands/watchlist.yml")
CATALOG_PATH      = os.environ.get("CATALOG_PATH",       "conf/products/catalog.yml")
DB_PATH           = os.environ.get("DB_PATH",            "data/deals.duckdb")


def load_adapter_configs() -> list[dict]:
    configs = []
    for path in glob.glob(f"{ADAPTER_CONF_DIR}/*.yml"):
        with open(path) as f:
            cfg = yaml.safe_load(f)
        if cfg.get("enabled", True):
            # inject catalog into product_page adapter
            if cfg.get("adapter") == "product_page":
                with open(cfg.get("catalog_path", CATALOG_PATH)) as f:
                    cfg["catalog"] = yaml.safe_load(f)
            configs.append(cfg)
    return configs


async def run_flyer_adapter(cfg: dict, matcher: BrandMatcher, db: Database):
    """Run a flyer/ad adapter — matches deals and sends email digest."""
    adapter = load_adapter(cfg)
    print(f"[scheduler] running flyer adapter: {adapter.name} ({cfg.get('store', '')})")
    try:
        items   = await adapter.fetch()
        matches = matcher.match(items)
        db.save_items(items)
        db.save_matches(matches)
        print(f"[scheduler] {adapter.name}: {len(items)} items, {len(matches)} matches")

        if matches:
            comparator   = PriceComparator(DB_PATH)
            comparisons  = comparator.compare_all()
            stores       = list({m.item.store for m in matches})
            digest       = build_digest(matches, stores, comparisons)
            send_digest(digest)
    except Exception as e:
        print(f"[scheduler] ERROR in {adapter.name}: {e}")


async def run_product_page_adapter(cfg: dict, db: Database):
    """Run the product page adapter — scrapes everyday prices, no email."""
    from src.models import PriceRecord
    adapter = load_adapter(cfg)
    print(f"[scheduler] running product page scrape")
    try:
        items = await adapter.fetch()
        # convert AdItems → PriceRecords using product_key embedded in source_url
        records = []
        catalog = cfg.get("catalog", {})
        url_to_key = {}
        for cat_slug, cat in catalog.get("categories", {}).items():
            for prod_slug, prod in cat.get("products", {}).items():
                for store, url in prod.get("stores", {}).items():
                    url_to_key[url] = (f"{cat_slug}/{prod_slug}", cat.get("label", cat_slug))

        for item in items:
            key, cat = url_to_key.get(item.source_url, (item.product_name, item.category or ""))
            records.append(PriceRecord(
                product_key  = key,
                product_name = item.product_name,
                category     = cat,
                store        = item.store,
                unit         = item.unit,
                price        = item.sale_price,
                price_type   = item.price_type,
                source_url   = item.source_url,
            ))

        db.save_price_records(records)
        print(f"[scheduler] product_page: {len(records)} price records saved")
    except Exception as e:
        print(f"[scheduler] ERROR in product_page: {e}")


def start():
    matcher  = BrandMatcher(WATCHLIST_PATH)
    db       = Database(DB_PATH)
    configs  = load_adapter_configs()
    scheduler = AsyncIOScheduler()

    for cfg in configs:
        schedule    = cfg.get("schedule", "0 7 * * 2")
        adapter_key = cfg.get("adapter", "unknown")
        job_id      = f"{adapter_key}_{cfg.get('store', cfg.get('postal_code', 'default'))}"

        if adapter_key == "product_page":
            scheduler.add_job(
                run_product_page_adapter,
                CronTrigger.from_crontab(schedule),
                args=[cfg, db],
                id=job_id,
                max_instances=1,
                coalesce=True,
            )
        else:
            scheduler.add_job(
                run_flyer_adapter,
                CronTrigger.from_crontab(schedule),
                args=[cfg, matcher, db],
                id=job_id,
                max_instances=1,
                coalesce=True,
            )
        print(f"[scheduler] registered {adapter_key} ({job_id}) @ {schedule}")

    scheduler.start()
    print("[scheduler] running. Press Ctrl+C to stop.")

    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        db.close()


if __name__ == "__main__":
    start()
