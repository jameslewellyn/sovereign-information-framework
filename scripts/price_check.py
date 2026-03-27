#!/usr/bin/env python3
"""
price_check — query current prices from the local database.

Usage:
  python scripts/price_check.py milk
  python scripts/price_check.py --category dairy
  python scripts/price_check.py --product-key dairy/whole-milk
  python scripts/price_check.py --all
  python scripts/price_check.py --all --sort unit_price
  python scripts/price_check.py milk --history

Run from the deals-tracker root directory.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# allow running from project root without installing as a package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.db import Database


# ── ANSI colours (disabled if stdout is not a tty) ────────────────────────────

USE_COLOUR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOUR else text

GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33",   t)
RED    = lambda t: _c("31",   t)
CYAN   = lambda t: _c("36",   t)
BOLD   = lambda t: _c("1",    t)
DIM    = lambda t: _c("2",    t)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_price(p) -> str:
    return f"${p:.2f}" if p is not None else "—"

def _fmt_unit(p, unit) -> str:
    if p is None or unit is None:
        return "—"
    return f"${p:.4f}/{unit}"

def _fmt_trend(trend: str | None) -> str:
    if trend == "falling": return GREEN("↓ falling")
    if trend == "rising":  return RED("↑ rising")
    if trend == "stable":  return DIM("→ stable")
    return DIM("—")

def _fmt_vs_avg(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct <= -20: return GREEN(f"↓{abs(pct):.0f}% vs avg")
    if pct <= -10: return GREEN(f"↓{abs(pct):.0f}% vs avg")
    if pct >= 15:  return RED(f"↑{pct:.0f}% vs avg")
    return ""

def _fmt_age(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    days  = delta.days
    if days == 0:   return GREEN("today")
    if days == 1:   return "yesterday"
    if days < 7:    return f"{days}d ago"
    if days < 31:   return f"{days // 7}w ago"
    return YELLOW(f"{days // 30}mo ago")

def _row_divider(widths: list[int]) -> str:
    return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

def _row(cells: list[str], widths: list[int]) -> str:
    # strip ANSI for width calculation
    import re
    ansi_escape = re.compile(r"\033\[[0-9;]*m")
    parts = []
    for cell, width in zip(cells, widths):
        visible = ansi_escape.sub("", cell)
        pad     = width - len(visible)
        parts.append(f" {cell}{' ' * pad} ")
    return "|" + "|".join(parts) + "|"

def _print_table(headers: list[str], rows: list[list[str]]):
    import re
    ansi_escape = re.compile(r"\033\[[0-9;]*m")
    all_rows = [headers] + rows
    widths = [
        max(len(ansi_escape.sub("", r[i])) for r in all_rows)
        for i in range(len(headers))
    ]
    div = _row_divider(widths)
    print(div)
    print(_row([BOLD(h) for h in headers], widths))
    print(div)
    for r in rows:
        print(_row(r, widths))
    print(div)


# ── Core query functions ──────────────────────────────────────────────────────

def search_products(db: Database, query: str) -> list[str]:
    """Return product_keys matching a name/slug fragment."""
    rows = db.conn.execute("""
        SELECT DISTINCT product_key, canonical_name
        FROM price_observations
        WHERE lower(product_key)     LIKE lower(?)
           OR lower(canonical_name)  LIKE lower(?)
           OR lower(product_name)    LIKE lower(?)
        ORDER BY product_key
    """, [f"%{query}%"] * 3).fetchall()
    return rows   # [(product_key, canonical_name), ...]


def show_upcoming(db: Database, product_key: str | None = None):
    """Print upcoming sales, optionally filtered to one product."""
    rows = db.get_upcoming_sales(days_ahead=21)
    if product_key:
        rows = [r for r in rows if r["product_key"] == product_key]
    if not rows:
        return

    print(f"\n{BOLD('⏳ Upcoming Sales')}  {DIM('(starts in the future — plan ahead)')}\n")
    table_rows = []
    from datetime import date as _date
    today = _date.today()
    for r in rows:
        days_until = (r["valid_from"] - today).days if r["valid_from"] else "?"
        day_str    = (f"in {days_until}d" if isinstance(days_until, int) else "?")
        starts     = r["valid_from"].strftime("%a %b %d") if r["valid_from"] else "?"
        ends       = r["valid_until"].strftime("– %b %d") if r["valid_until"] else ""
        up_str     = _fmt_unit(r["unit_price"], r["base_unit"])
        disc_str   = f"↓{r['discount_pct']:.0f}%" if r["discount_pct"] else ""
        table_rows.append([
            YELLOW(day_str),
            r["store"],
            r["canonical_name"] or r["product_name"],
            r["pack_description"] or "—",
            _fmt_price(r["pack_price"]),
            up_str,
            disc_str,
            f"{starts} {ends}".strip(),
        ])
    _print_table(
        ["Starts", "Store", "Product", "Pack", "Sale Price", "Unit Price", "Disc", "Dates"],
        table_rows,
    )


def show_wait_signals(db: Database, product_key: str | None = None):
    """Print WaitSignals — items cheaper to wait for than buy today."""
    from src.comparators.price_comparator import PriceComparator
    comp    = PriceComparator(db)
    signals = comp.build_wait_signals(days_ahead=21)
    if product_key:
        signals = [s for s in signals if s.product_key == product_key]
    if not signals:
        return

    print(f"\n{BOLD('⚠  Wait Before You Buy')}  "
          f"{DIM('upcoming sale prices beat what you can get today')}\n")
    table_rows = []
    for s in signals:
        up     = s.upcoming
        base   = s.base_unit or ""
        starts = up.valid_from.strftime("%a %b %d")
        table_rows.append([
            RED(f"↓{s.pct_savings:.0f}%"),
            s.canonical_name,
            f"{s.today_store} now: {_fmt_unit(s.today_unit_price, base)}",
            f"{up.store} {starts}: {_fmt_unit(up.sale_unit_price, base)}",
            GREEN(f"save ${s.unit_savings:.4f}/{base}"),
            f"in {up.days_until}d",
        ])
    _print_table(
        ["Save", "Product", "Today (worst)", "Upcoming sale", "Per-unit saving", "Starts"],
        table_rows,
    )


def show_comparison(db: Database, product_key: str, canonical_name: str):
    """Print cross-store unit price comparison for one product."""
    rows = db.conn.execute("""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY store ORDER BY observed_at DESC
            ) rn
            FROM price_observations WHERE product_key = ?
        )
        SELECT store, product_name, pack_price, unit_price, base_unit,
               pack_description, price_type, observed_at
        FROM ranked WHERE rn = 1
        ORDER BY unit_price NULLS LAST, pack_price
    """, [product_key]).fetchall()

    if not rows:
        print(f"  {DIM('No data in database for')} {product_key}")
        return

    print(f"\n{BOLD(canonical_name)}  {DIM('(' + product_key + ')')}")

    # find best unit price
    priced = [(r[0], r[2], r[3], r[4], r[5], r[7]) for r in rows if r[3] is not None]
    best_up = min((r[1] for r in priced), default=None)

    table_rows = []
    for store, prod_name, pack_p, unit_p, base_unit, pack_desc, price_type, obs_at in rows:
        stats = db.get_stats(product_key, store)

        store_cell = BOLD(store)
        pack_cell  = pack_desc or "—"
        price_cell = _fmt_price(pack_p)
        age_cell   = _fmt_age(obs_at)

        if unit_p is not None:
            up_str = _fmt_unit(unit_p, base_unit)
            if unit_p == best_up:
                up_str = GREEN(up_str) + " ✓"
        else:
            up_str = DIM("—")

        trend_cell  = _fmt_trend(stats.price_trend if stats else None)
        vs_avg_cell = _fmt_vs_avg(stats.vs_30d_avg_pct if stats else None)
        low_cell    = GREEN("★ ATL") if stats and stats.is_all_time_low else ""
        badge       = " ".join(b for b in [vs_avg_cell, low_cell] if b)

        table_rows.append([
            store_cell, pack_cell, price_cell, up_str, trend_cell, badge, age_cell
        ])

    _print_table(
        ["Store", "Pack", "Pack Price", "Unit Price", "Trend", "Signal", "Last Seen"],
        table_rows,
    )

    # savings summary
    if len(priced) >= 2:
        best_store  = min(priced, key=lambda r: r[1])[0]
        worst_up    = max(r[1] for r in priced)
        savings     = worst_up - best_up
        pct         = savings / worst_up * 100 if worst_up else 0
        base        = priced[0][3] or ""
        print(f"  {GREEN('Best:')} {best_store}  "
              f"saves {GREEN(f'${savings:.4f}/{base}')} "
              f"({GREEN(f'{pct:.0f}%')}) vs most expensive store\n")


def show_history(db: Database, product_key: str, days: int = 60):
    """Print recent price history across all stores."""
    rows = db.get_price_history(product_key, days=days)
    if not rows:
        print(f"  {DIM('No history found.')}")
        return

    print(f"\n{BOLD('Price history')} — last {days} days\n")
    table_rows = []
    for r in rows[:40]:
        obs = r["observed_at"]
        date_str = obs.strftime("%Y-%m-%d") if isinstance(obs, datetime) else str(obs)[:10]
        table_rows.append([
            date_str,
            r["store"],
            _fmt_price(r["pack_price"]),
            _fmt_unit(r["unit_price"], r["base_unit"]),
            r["pack_description"] or "—",
            r["price_type"],
        ])

    _print_table(
        ["Date", "Store", "Pack Price", "Unit Price", "Pack", "Type"],
        table_rows,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Query current prices from the deals-tracker database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/price_check.py milk
  python scripts/price_check.py --category dairy
  python scripts/price_check.py --product-key dairy/whole-milk
  python scripts/price_check.py --all
  python scripts/price_check.py milk --history
  python scripts/price_check.py --all --sort unit_price
        """,
    )
    parser.add_argument("query",          nargs="?",           help="Product name search term")
    parser.add_argument("--category",     "-c",                help="Filter by category")
    parser.add_argument("--product-key",  "-k",                help="Exact product key (e.g. dairy/whole-milk)")
    parser.add_argument("--all",          "-a", action="store_true", help="Show all tracked products")
    parser.add_argument("--history",      action="store_true", help="Show price history for matched products")
    parser.add_argument("--days",         type=int, default=60, help="History window in days (default: 60)")
    parser.add_argument("--db",           default="data/deals.duckdb", help="Path to DuckDB file")
    parser.add_argument("--sort",         choices=["unit_price", "store", "last_seen"],
                        default="unit_price", help="Sort column for comparison")
    parser.add_argument("--upcoming",     action="store_true",
                        help="Show upcoming sales from future flyers")
    parser.add_argument("--wait",         action="store_true",
                        help="Show wait signals (upcoming sales cheaper than today)")
    args = parser.parse_args()

    # ── open DB ───────────────────────────────────────────────────────────────
    db_path = Path(args.db)
    if not db_path.exists():
        print(RED(f"Database not found: {db_path}"))
        print(DIM("Run  python scripts/update_prices.py  to populate it first."))
        sys.exit(1)

    db = Database(str(db_path))

    # ── resolve which product keys to show ────────────────────────────────────
    targets: list[tuple[str, str]] = []   # [(product_key, canonical_name)]

    if args.product_key:
        targets = [(args.product_key, args.product_key)]

    elif args.query:
        matches = search_products(db, args.query)
        if not matches:
            print(RED(f"No products found matching '{args.query}'"))
            db.close()
            sys.exit(0)
        targets = matches

    elif args.category:
        rows = db.conn.execute("""
            SELECT DISTINCT product_key, canonical_name
            FROM price_observations
            WHERE lower(category) LIKE lower(?)
            ORDER BY product_key
        """, [f"%{args.category}%"]).fetchall()
        if not rows:
            print(RED(f"No products found in category '{args.category}'"))
            db.close()
            sys.exit(0)
        targets = rows

    elif args.all:
        rows = db.conn.execute("""
            SELECT DISTINCT product_key, canonical_name
            FROM price_observations
            ORDER BY category, product_key
        """).fetchall()
        targets = rows

    else:
        parser.print_help()
        db.close()
        sys.exit(0)

    # ── summary header ────────────────────────────────────────────────────────
    row_count = db.conn.execute(
        "SELECT COUNT(*) FROM price_observations"
    ).fetchone()[0]
    store_count = db.conn.execute(
        "SELECT COUNT(DISTINCT store) FROM price_observations"
    ).fetchone()[0]

    print(f"\n{BOLD('Price Check')} — "
          f"{CYAN(str(row_count))} observations · "
          f"{CYAN(str(store_count))} stores · "
          f"db: {DIM(str(db_path))}")
    print(DIM("─" * 70))

    # ── wait signals / upcoming (shown once, not per-product) ─────────────────
    single_key = targets[0][0] if len(targets) == 1 else None

    if args.wait or (not args.upcoming and not targets and not args.all):
        show_wait_signals(db, product_key=single_key)

    if args.upcoming:
        show_upcoming(db, product_key=single_key)

    # ── per-product comparison tables ─────────────────────────────────────────
    for product_key, canonical_name in targets:
        show_comparison(db, product_key, canonical_name or product_key)
        if args.history:
            show_history(db, product_key, days=args.days)
        # always show wait signals inline when drilling into a single product
        if len(targets) == 1:
            show_wait_signals(db, product_key=product_key)
            show_upcoming(db, product_key=product_key)

    db.close()


if __name__ == "__main__":
    main()
