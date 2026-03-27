"""
Database layer — DuckDB with schema versioning.

Tables:
  schema_version     — migration tracking
  price_observations — every price data point ever scraped (append-only)
  price_stats        — rolling aggregates per (product_key, store), updated on insert
  products           — catalog of tracked products (from catalog.yml)
  stores             — known stores and their locations

Design principles:
  - price_observations is APPEND-ONLY. Never update or delete rows.
    This preserves full history for trend analysis.
  - price_stats is a MUTABLE summary table, recomputed after each batch insert.
  - All prices stored as DOUBLE (float64). Unit prices stored to 4 decimal places.
  - Timestamps stored in UTC.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import duckdb

from src.models import AdItem, PriceObservation, PriceStats


CURRENT_SCHEMA_VERSION = 3


class Database:

    def __init__(self, path: str = "data/deals.duckdb"):
        self.path = path
        self.conn = duckdb.connect(path)
        self._migrate()

    # ── Schema & migrations ───────────────────────────────────────────────

    def _migrate(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TIMESTAMP DEFAULT current_timestamp,
                description VARCHAR
            )
        """)
        current = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0]

        if current < 1:
            self._apply_migration(1, "initial schema", self._migration_1)
        if current < 2:
            self._apply_migration(2, "add price_stats table", self._migration_2)
        if current < 3:
            self._apply_migration(3, "add products and stores tables", self._migration_3)

    def _apply_migration(self, version: int, description: str, fn):
        fn()
        self.conn.execute(
            "INSERT INTO schema_version VALUES (?, current_timestamp, ?)",
            [version, description],
        )
        print(f"[db] applied migration {version}: {description}")

    def _migration_1(self):
        """Core price observations table."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS price_observations (
                -- identity
                id              VARCHAR PRIMARY KEY,
                product_key     VARCHAR NOT NULL,
                canonical_name  VARCHAR,
                category        VARCHAR,
                subcategory     VARCHAR,
                brand           VARCHAR,

                -- store
                store           VARCHAR NOT NULL,
                store_location  VARCHAR,
                chain           VARCHAR,

                -- product as listed
                product_name    VARCHAR,

                -- pack info
                pack_qty        DOUBLE,
                pack_unit       VARCHAR,
                inner_qty       DOUBLE,
                inner_unit      VARCHAR,
                pack_description VARCHAR,   -- raw string e.g. "30 rolls"
                total_base_units DOUBLE,    -- computed: e.g. 30 rolls
                base_unit       VARCHAR,    -- e.g. "roll"

                -- prices
                pack_price      DOUBLE NOT NULL,
                unit_price      DOUBLE,     -- pack_price / total_base_units
                price_type      VARCHAR DEFAULT 'everyday',

                -- sale metadata
                original_pack_price  DOUBLE,
                original_unit_price  DOUBLE,
                discount_pct         DOUBLE,
                valid_from           DATE,
                valid_until          DATE,

                -- provenance
                source          VARCHAR,
                source_url      VARCHAR,
                observed_at     TIMESTAMP NOT NULL,
                flyer_id        VARCHAR,

                -- indexing hints
                observed_date   DATE GENERATED ALWAYS AS (observed_at::DATE) VIRTUAL
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_obs_product_store
            ON price_observations(product_key, store, observed_at)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_obs_category
            ON price_observations(category, observed_at)
        """)

    def _migration_2(self):
        """Rolling stats table — recomputed after each batch."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS price_stats (
                product_key         VARCHAR,
                store               VARCHAR,
                base_unit           VARCHAR,

                -- current
                current_pack_price  DOUBLE,
                current_unit_price  DOUBLE,
                last_observed_at    TIMESTAMP,

                -- 30-day
                avg_unit_price_30d  DOUBLE,
                min_unit_price_30d  DOUBLE,
                max_unit_price_30d  DOUBLE,

                -- 90-day
                avg_unit_price_90d  DOUBLE,
                min_unit_price_90d  DOUBLE,
                max_unit_price_90d  DOUBLE,
                obs_count_90d       INTEGER,

                -- all-time
                min_unit_price_ever DOUBLE,
                first_observed_at   TIMESTAMP,

                -- signals (recomputed)
                vs_30d_avg_pct      DOUBLE,   -- % vs 30d avg (negative = cheaper)
                price_trend         VARCHAR,  -- rising/falling/stable
                is_all_time_low     BOOLEAN DEFAULT FALSE,

                updated_at          TIMESTAMP DEFAULT current_timestamp,

                PRIMARY KEY (product_key, store)
            )
        """)

    def _migration_3(self):
        """Product catalog and store registry."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                product_key     VARCHAR PRIMARY KEY,
                canonical_name  VARCHAR,
                category        VARCHAR,
                subcategory     VARCHAR,
                base_unit       VARCHAR,   -- canonical comparison unit
                notes           VARCHAR,
                added_at        TIMESTAMP DEFAULT current_timestamp
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stores (
                store_key       VARCHAR PRIMARY KEY,   -- e.g. "costco-east-peoria"
                chain           VARCHAR,               -- e.g. "Costco"
                display_name    VARCHAR,               -- e.g. "Costco East Peoria"
                city            VARCHAR,
                state           VARCHAR,
                zip             VARCHAR,
                added_at        TIMESTAMP DEFAULT current_timestamp
            )
        """)

    # ── Write operations ──────────────────────────────────────────────────

    def save_observations(self, observations: list[PriceObservation]):
        """
        Insert new price observations. Skips duplicates by (product_key, store, pack_price, observed_date).
        """
        if not observations:
            return

        rows = []
        for o in observations:
            obs_id = o.id or str(uuid.uuid4())
            ps = o.pack_size
            rows.append((
                obs_id,
                o.product_key, o.canonical_name, o.category, o.subcategory, o.brand,
                o.store, o.store_location, o.chain,
                o.product_name,
                ps.quantity        if ps else None,
                ps.unit            if ps else None,
                ps.inner_quantity  if ps else None,
                ps.inner_unit      if ps else None,
                str(ps)            if ps else o.base_unit,
                ps.total_base_units if ps else None,
                o.base_unit,
                o.pack_price,
                o.unit_price,
                o.price_type,
                o.original_pack_price,
                o.original_unit_price,
                o.discount_pct,
                o.valid_from,
                o.valid_until,
                o.source,
                o.source_url,
                o.observed_at,
                o.flyer_id,
            ))

        self.conn.executemany("""
            INSERT OR IGNORE INTO price_observations (
                id, product_key, canonical_name, category, subcategory, brand,
                store, store_location, chain,
                product_name,
                pack_qty, pack_unit, inner_qty, inner_unit, pack_description,
                total_base_units, base_unit,
                pack_price, unit_price, price_type,
                original_pack_price, original_unit_price, discount_pct,
                valid_from, valid_until,
                source, source_url, observed_at, flyer_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)

        # refresh stats for affected (product_key, store) pairs
        keys = list({(o.product_key, o.store) for o in observations})
        for product_key, store in keys:
            self._refresh_stats(product_key, store)

    def save_from_ad_items(
        self,
        items: list[AdItem],
        product_key: str,
        catalog_unit: Optional[str] = None,
    ):
        """
        Convert AdItems to PriceObservations using the unit normalizer.
        Used when the adapter doesn't have catalog context.
        """
        from src.storage.unit_normalizer import enrich_with_unit_price

        observations = []
        for item in items:
            unit_price, base_unit, pack_size = enrich_with_unit_price(
                item.sale_price,
                item.unit,
                catalog_unit,
            )
            orig_unit = None
            if item.original_price and pack_size:
                total = pack_size.total_base_units
                orig_unit = round(item.original_price / total, 4) if total > 0 else None

            observations.append(PriceObservation(
                product_key    = product_key,
                canonical_name = item.product_name,
                category       = item.category or "",
                brand          = item.brand,
                store          = item.store,
                product_name   = item.product_name,
                pack_size      = pack_size,
                pack_price     = item.sale_price,
                unit_price     = unit_price,
                base_unit      = base_unit,
                price_type     = item.price_type,
                original_pack_price = item.original_price,
                original_unit_price = orig_unit,
                discount_pct   = item.discount_pct,
                valid_from     = item.valid_from,
                valid_until    = item.valid_until,
                source         = item.source,
                source_url     = item.source_url,
                observed_at    = item.scraped_at,
            ))

        self.save_observations(observations)

    # ── Stats refresh ─────────────────────────────────────────────────────

    def _refresh_stats(self, product_key: str, store: str):
        """Recompute price_stats for one (product_key, store) pair."""
        row = self.conn.execute("""
            WITH
            current AS (
                SELECT pack_price, unit_price, base_unit, observed_at
                FROM price_observations
                WHERE product_key = ? AND store = ?
                ORDER BY observed_at DESC LIMIT 1
            ),
            stats_30 AS (
                SELECT AVG(unit_price) avg, MIN(unit_price) mn, MAX(unit_price) mx
                FROM price_observations
                WHERE product_key = ? AND store = ?
                  AND unit_price IS NOT NULL
                  AND observed_at > now() - interval '30 days'
            ),
            stats_90 AS (
                SELECT AVG(unit_price) avg, MIN(unit_price) mn, MAX(unit_price) mx,
                       COUNT(*) cnt
                FROM price_observations
                WHERE product_key = ? AND store = ?
                  AND unit_price IS NOT NULL
                  AND observed_at > now() - interval '90 days'
            ),
            alltime AS (
                SELECT MIN(unit_price) mn, MIN(observed_at) first_obs
                FROM price_observations
                WHERE product_key = ? AND store = ? AND unit_price IS NOT NULL
            )
            SELECT
                c.pack_price, c.unit_price, c.base_unit, c.observed_at,
                s30.avg, s30.mn, s30.mx,
                s90.avg, s90.mn, s90.mx, s90.cnt,
                alltime.mn, alltime.first_obs
            FROM current c, stats_30 s30, stats_90 s90, alltime
        """, [product_key, store] * 4).fetchone()

        if not row:
            return

        (pack_p, unit_p, base_unit, last_obs,
         avg30, min30, max30,
         avg90, min90, max90, cnt90,
         min_ever, first_obs) = row

        vs_30d = None
        if unit_p and avg30:
            vs_30d = round((unit_p - avg30) / avg30 * 100, 1)

        # trend: compare last 3 observations
        trend = self._compute_trend(product_key, store)
        is_low = bool(unit_p and min_ever and unit_p <= min_ever * 1.01)

        now = datetime.now(timezone.utc)
        self.conn.execute("""
            INSERT INTO price_stats (
                product_key, store, base_unit,
                current_pack_price, current_unit_price, last_observed_at,
                avg_unit_price_30d, min_unit_price_30d, max_unit_price_30d,
                avg_unit_price_90d, min_unit_price_90d, max_unit_price_90d, obs_count_90d,
                min_unit_price_ever, first_observed_at,
                vs_30d_avg_pct, price_trend, is_all_time_low, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product_key, store) DO UPDATE SET
                base_unit           = excluded.base_unit,
                current_pack_price  = excluded.current_pack_price,
                current_unit_price  = excluded.current_unit_price,
                last_observed_at    = excluded.last_observed_at,
                avg_unit_price_30d  = excluded.avg_unit_price_30d,
                min_unit_price_30d  = excluded.min_unit_price_30d,
                max_unit_price_30d  = excluded.max_unit_price_30d,
                avg_unit_price_90d  = excluded.avg_unit_price_90d,
                min_unit_price_90d  = excluded.min_unit_price_90d,
                max_unit_price_90d  = excluded.max_unit_price_90d,
                obs_count_90d       = excluded.obs_count_90d,
                min_unit_price_ever = excluded.min_unit_price_ever,
                vs_30d_avg_pct      = excluded.vs_30d_avg_pct,
                price_trend         = excluded.price_trend,
                is_all_time_low     = excluded.is_all_time_low,
                updated_at          = excluded.updated_at
        """, [
            product_key, store, base_unit,
            pack_p, unit_p, last_obs,
            avg30, min30, max30,
            avg90, min90, max90, cnt90,
            min_ever, first_obs,
            vs_30d, trend, is_low,
            now,
        ])

    def _compute_trend(self, product_key: str, store: str) -> Optional[str]:
        rows = self.conn.execute("""
            SELECT unit_price FROM price_observations
            WHERE product_key = ? AND store = ? AND unit_price IS NOT NULL
            ORDER BY observed_at DESC LIMIT 5
        """, [product_key, store]).fetchall()

        prices = [r[0] for r in rows]
        if len(prices) < 3:
            return None
        if prices[0] < prices[-1] * 0.97:
            return "falling"
        if prices[0] > prices[-1] * 1.03:
            return "rising"
        return "stable"

    # ── Read operations ───────────────────────────────────────────────────

    def get_stats(self, product_key: str, store: str) -> Optional[PriceStats]:
        row = self.conn.execute("""
            SELECT * FROM price_stats WHERE product_key = ? AND store = ?
        """, [product_key, store]).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.description]
        d = dict(zip(cols, row))
        return PriceStats(
            product_key         = d["product_key"],
            store               = d["store"],
            base_unit           = d["base_unit"],
            current_pack_price  = d["current_pack_price"],
            current_unit_price  = d["current_unit_price"],
            last_observed_at    = d["last_observed_at"],
            avg_unit_price_30d  = d["avg_unit_price_30d"],
            min_unit_price_30d  = d["min_unit_price_30d"],
            max_unit_price_30d  = d["max_unit_price_30d"],
            avg_unit_price_90d  = d["avg_unit_price_90d"],
            min_unit_price_90d  = d["min_unit_price_90d"],
            max_unit_price_90d  = d["max_unit_price_90d"],
            observation_count_90d = d["obs_count_90d"] or 0,
            vs_30d_avg_pct      = d["vs_30d_avg_pct"],
            price_trend         = d["price_trend"],
            is_all_time_low     = bool(d["is_all_time_low"]),
        )

    def get_price_history(
        self,
        product_key: str,
        store: Optional[str] = None,
        days: int = 90,
    ) -> list[dict]:
        # DuckDB requires integer literals in INTERVAL — inject as int (safe, no user input)
        days_int = int(days)
        where = f"WHERE product_key = ? AND observed_at > now() - INTERVAL '{days_int} days'"
        params = [product_key]
        if store:
            where += " AND store = ?"
            params.append(store)
        rows = self.conn.execute(f"""
            SELECT store, product_name, pack_price, unit_price, base_unit,
                   pack_description, price_type, observed_at
            FROM price_observations
            {where}
            ORDER BY observed_at DESC
        """, params).fetchall()
        cols = ["store","product_name","pack_price","unit_price","base_unit",
                "pack_description","price_type","observed_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_latest_per_store(self, product_key: str) -> list[dict]:
        """Latest observation per store for a product."""
        rows = self.conn.execute("""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY store ORDER BY observed_at DESC
                ) rn
                FROM price_observations WHERE product_key = ?
            )
            SELECT store, product_name, pack_price, unit_price, base_unit,
                   pack_description, price_type, observed_at
            FROM ranked WHERE rn = 1
            ORDER BY unit_price NULLS LAST
        """, [product_key]).fetchall()
        cols = ["store","product_name","pack_price","unit_price","base_unit",
                "pack_description","price_type","observed_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_upcoming_sales(self, days_ahead: int = 14) -> list[dict]:
        """
        Return all sale observations whose valid_from is in the future
        (within the next `days_ahead` days).

        These are deals scraped from flyers before they start — the key
        input for WaitSignal generation.
        """
        rows = self.conn.execute(f"""
            SELECT product_key, canonical_name, category, brand,
                   store, product_name,
                   pack_price, unit_price, base_unit, pack_description,
                   original_pack_price, discount_pct,
                   valid_from, valid_until,
                   source_url, observed_at
            FROM price_observations
            WHERE price_type = 'sale'
              AND valid_from IS NOT NULL
              AND valid_from > current_date
              AND valid_from <= current_date + INTERVAL '{int(days_ahead)} days'
            ORDER BY valid_from, product_key, unit_price NULLS LAST
        """).fetchall()
        cols = [
            "product_key", "canonical_name", "category", "brand",
            "store", "product_name",
            "pack_price", "unit_price", "base_unit", "pack_description",
            "original_pack_price", "discount_pct",
            "valid_from", "valid_until",
            "source_url", "observed_at",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def get_current_everyday_prices(self, product_keys: list[str] | None = None) -> list[dict]:
        """
        Latest everyday (non-sale) price per (product_key, store).
        Used as the 'what you'd pay today' baseline for WaitSignal comparisons.
        """
        where = "WHERE price_type = 'everyday'"
        params: list = []
        if product_keys:
            placeholders = ",".join(["?"] * len(product_keys))
            where += f" AND product_key IN ({placeholders})"
            params = list(product_keys)

        rows = self.conn.execute(f"""
            WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY product_key, store ORDER BY observed_at DESC
                ) rn
                FROM price_observations {where}
            )
            SELECT product_key, canonical_name, category, store,
                   pack_price, unit_price, base_unit, pack_description,
                   observed_at
            FROM ranked WHERE rn = 1
            ORDER BY product_key, unit_price NULLS LAST
        """, params).fetchall()
        cols = ["product_key", "canonical_name", "category", "store",
                "pack_price", "unit_price", "base_unit", "pack_description",
                "observed_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_active_sales(self) -> list[dict]:
        """
        Sale observations that are valid today (valid_from <= today <= valid_until).
        """
        rows = self.conn.execute("""
            SELECT product_key, canonical_name, category, brand,
                   store, product_name,
                   pack_price, unit_price, base_unit, pack_description,
                   original_pack_price, discount_pct,
                   valid_from, valid_until, source_url
            FROM price_observations
            WHERE price_type = 'sale'
              AND (valid_from IS NULL OR valid_from <= current_date)
              AND (valid_until IS NULL OR valid_until >= current_date)
            ORDER BY discount_pct DESC NULLS LAST
        """).fetchall()
        cols = [
            "product_key", "canonical_name", "category", "brand",
            "store", "product_name",
            "pack_price", "unit_price", "base_unit", "pack_description",
            "original_pack_price", "discount_pct",
            "valid_from", "valid_until", "source_url",
        ]
        return [dict(zip(cols, r)) for r in rows]

    def close(self):
        self.conn.close()
