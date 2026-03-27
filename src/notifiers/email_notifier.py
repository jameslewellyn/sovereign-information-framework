"""
Email notifier — weekly digest with two sections:
  1. This week's matched deals (sale prices vs watchlist)
  2. Cross-store price comparison (unit-price normalized, with trend badges)
"""

from __future__ import annotations

import os
import smtplib
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.models import (
    ComparisonRow, Digest, DigestEntry, MatchedDeal, PriceStats,
    UpcomingSale, WaitSignal,
)


# ── Digest builder ────────────────────────────────────────────────────────────

def build_digest(
    matches:      list[MatchedDeal],
    stores:       list[str],
    comparisons:  list[ComparisonRow] = None,
    wait_signals: list = None,
    upcoming:     list = None,
) -> Digest:
    entries = []
    for m in matches:
        item = m.item
        obs  = m.observation

        unit_price   = obs.unit_price        if obs else None
        base_unit    = obs.base_unit         if obs else None
        pack_desc    = str(obs.pack_size)    if obs and obs.pack_size else item.unit
        orig_unit    = obs.original_unit_price if obs else None
        vs_avg_label = m.vs_avg_label

        obs = m.observation
        sale_status = obs.sale_status if obs else "active"

        entries.append(DigestEntry(
            category            = m.matched_category,
            brand               = m.matched_brand,
            product_name        = item.product_name,
            store               = item.store,
            pack_price          = item.sale_price,
            unit_price          = unit_price,
            base_unit           = base_unit,
            pack_description    = pack_desc,
            original_pack_price = item.original_price,
            discount_pct        = item.discount_pct,
            valid_from          = item.valid_from,
            valid_until         = item.valid_until,
            sale_status         = sale_status,
            source_url          = item.source_url,
            price_type          = item.price_type,
            vs_avg_label        = vs_avg_label,
            is_all_time_low     = bool(m.stats and m.stats.is_all_time_low),
        ))

    # split active vs upcoming
    active_entries   = [e for e in entries if e.sale_status != "upcoming"]
    upcoming_entries = [e for e in entries if e.sale_status == "upcoming"]

    return Digest(
        entries        = active_entries,
        upcoming       = upcoming_entries,
        wait_signals   = wait_signals or [],
        comparisons    = comparisons or [],
        total_deals    = len(active_entries),
        stores_covered = stores,
    )


# ── HTML rendering ────────────────────────────────────────────────────────────

def _render_html(digest: Digest) -> str:
    deals_html      = _render_deals(digest.entries)
    wait_html       = _render_wait_signals(digest.wait_signals)
    upcoming_html   = _render_upcoming(digest.upcoming)
    comparison_html = _render_comparisons(digest.comparisons)
    date_str        = digest.generated_at.strftime("%A %b %d, %Y")
    stores_str      = ", ".join(digest.stores_covered)
    n_wait          = len(digest.wait_signals)
    n_upcoming      = len(digest.upcoming)

    summary_parts = [f"{digest.total_deals} deals"]
    if n_wait:      summary_parts.append(f"{n_wait} wait alerts")
    if n_upcoming:  summary_parts.append(f"{n_upcoming} upcoming sales")
    summary = " · ".join(summary_parts)

    return f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             max-width:960px;margin:auto;padding:20px;color:#222">

  <h2 style="margin-bottom:4px">🛒 Weekly Price Digest — {date_str}</h2>
  <p style="color:#666;margin-top:0">{summary} · stores: {stores_str}</p>

  {wait_html}
  {deals_html}
  {upcoming_html}
  {comparison_html}

  <p style="color:#aaa;font-size:11px;margin-top:40px;border-top:1px solid #eee;padding-top:12px">
    Generated {digest.generated_at.isoformat()} · deals-tracker ·
    Unit prices normalize pack sizes for fair comparison.
  </p>
</body>
</html>"""


def _render_deals(entries: list[DigestEntry]) -> str:
    if not entries:
        return "<p style='color:#888'>No matched deals this week.</p>"

    rows = ""
    current_cat = None
    for e in entries:
        if e.category != current_cat:
            current_cat = e.category
            rows += f"""
            <tr>
              <td colspan="6"
                  style="background:#f5f5f5;font-weight:600;padding:8px 10px;
                         text-transform:uppercase;font-size:11px;letter-spacing:.5px;
                         color:#555;border-top:2px solid #ddd">
                {e.category}
              </td>
            </tr>"""

        # price cell
        orig_str = f"<s style='color:#aaa;font-size:11px'>${e.original_pack_price:.2f}</s> " \
                   if e.original_pack_price else ""
        disc_str = f"<span style='color:#e53;font-size:11px'>↓{e.discount_pct:.0f}%</span> " \
                   if e.discount_pct else ""
        price_cell = f"{orig_str}<strong>${e.pack_price:.2f}</strong> {disc_str}"

        # unit price cell
        if e.unit_price and e.base_unit:
            unit_cell = f"<span style='color:#555'>${e.unit_price:.3f}/{e.base_unit}</span>"
        else:
            unit_cell = "<span style='color:#ccc'>—</span>"

        # badges
        badges = ""
        if e.is_all_time_low:
            badges += "<span style='background:#ff9800;color:white;border-radius:3px;" \
                      "padding:1px 5px;font-size:10px;margin-left:4px'>ALL TIME LOW</span>"
        if e.vs_avg_label:
            color = "#4caf50" if "below" in e.vs_avg_label else "#f44336"
            badges += f"<span style='color:{color};font-size:11px;margin-left:4px'>{e.vs_avg_label}</span>"

        link   = f'<a href="{e.source_url}" style="color:#1976d2">view</a>' if e.source_url else ""
        until  = str(e.valid_until) if e.valid_until else "—"
        name   = e.product_name[:50]
        pack   = e.pack_description or ""

        rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:7px 10px">{name} {badges}</td>
          <td style="padding:7px 10px;color:#666;font-size:12px">{pack}</td>
          <td style="padding:7px 10px">{e.store}</td>
          <td style="padding:7px 10px">{price_cell}</td>
          <td style="padding:7px 10px">{unit_cell}</td>
          <td style="padding:7px 10px;color:#888;font-size:12px">{until} {link}</td>
        </tr>"""

    return f"""
  <h3 style="margin-top:30px">📋 This Week's Matched Deals</h3>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#333;color:white;font-size:12px">
        <th style="padding:8px 10px;text-align:left">Product</th>
        <th style="padding:8px 10px;text-align:left">Pack Size</th>
        <th style="padding:8px 10px;text-align:left">Store</th>
        <th style="padding:8px 10px;text-align:left">Pack Price</th>
        <th style="padding:8px 10px;text-align:left">Unit Price</th>
        <th style="padding:8px 10px;text-align:left">Valid Until</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>"""


def _render_wait_signals(signals: list[WaitSignal]) -> str:
    """
    Prominent 'Don't buy yet' banner at the top of the digest.
    Only shown when upcoming sales are meaningfully cheaper than today's price.
    """
    if not signals:
        return ""

    rows = ""
    for s in signals:
        up       = s.upcoming
        base     = s.base_unit or ""
        starts   = up.valid_from.strftime("%a %b %d")
        ends     = f" – {up.valid_until.strftime('%b %d')}" if up.valid_until else ""
        days_str = f"in {up.days_until} day{'s' if up.days_until != 1 else ''}"

        today_str    = f"${s.today_unit_price:.4f}/{base}" if base else f"${s.today_pack_price:.2f}"
        sale_str     = f"${up.sale_unit_price:.4f}/{base}" if (up.sale_unit_price and base) else f"${up.sale_pack_price:.2f}"
        pack_str     = up.pack_description or ""
        link         = f' <a href="{up.source_url}" style="color:#1976d2;font-size:11px">view</a>' if up.source_url else ""
        disc_str     = f" (↓{up.discount_pct:.0f}% off)" if up.discount_pct else ""

        rows += f"""
        <tr style="border-bottom:1px solid #ffe082">
          <td style="padding:9px 12px;font-weight:600">{s.canonical_name}</td>
          <td style="padding:9px 12px">{up.store}</td>
          <td style="padding:9px 12px;color:#555">{pack_str}{disc_str}{link}</td>
          <td style="padding:9px 12px;color:#888;text-decoration:line-through">{today_str} today</td>
          <td style="padding:9px 12px;font-weight:700;color:#e65100">{sale_str} {days_str}</td>
          <td style="padding:9px 12px;color:#2e7d32;font-weight:600">
            saves {s.pct_savings:.0f}%
          </td>
          <td style="padding:9px 12px;color:#666;font-size:12px">{starts}{ends}</td>
        </tr>"""

    return f"""
  <div style="background:#fff8e1;border:2px solid #ffc107;border-radius:6px;
              margin:20px 0;padding:4px 0">
    <h3 style="margin:12px 16px 4px">⏳ Wait Before You Buy</h3>
    <p style="margin:0 16px 10px;color:#666;font-size:12px">
      These items are going on sale soon. Buying today at the regular price would cost you more.
    </p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="background:#ffc107;color:#333;font-size:12px">
          <th style="padding:8px 12px;text-align:left">Product</th>
          <th style="padding:8px 12px;text-align:left">Sale at Store</th>
          <th style="padding:8px 12px;text-align:left">Pack</th>
          <th style="padding:8px 12px;text-align:left">Today's Price</th>
          <th style="padding:8px 12px;text-align:left">Upcoming Sale</th>
          <th style="padding:8px 12px;text-align:left">Savings</th>
          <th style="padding:8px 12px;text-align:left">Sale Dates</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>"""


def _render_upcoming(entries: list[DigestEntry]) -> str:
    """
    Calendar of future sales — items in flyers that haven't started yet.
    Lower priority than the WaitSignal banner; shows the full list.
    """
    if not entries:
        return ""

    rows = ""
    for e in sorted(entries, key=lambda x: x.valid_from or "9999"):
        starts   = e.valid_from.strftime("%a %b %d") if e.valid_from else "?"
        ends     = f" – {e.valid_until.strftime('%b %d')}" if e.valid_until else ""
        orig_str = f"<s style='color:#aaa'>${e.original_pack_price:.2f}</s> " if e.original_pack_price else ""
        disc_str = f" ↓{e.discount_pct:.0f}%" if e.discount_pct else ""
        up_str   = f"${e.unit_price:.4f}/{e.base_unit}" if (e.unit_price and e.base_unit) else "—"
        link     = f'<a href="{e.source_url}" style="color:#1976d2">view</a>' if e.source_url else ""

        rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0">
          <td style="padding:7px 10px">{e.product_name}</td>
          <td style="padding:7px 10px">{e.store}</td>
          <td style="padding:7px 10px">{e.pack_description or '—'}</td>
          <td style="padding:7px 10px">{orig_str}<strong>${e.pack_price:.2f}</strong>{disc_str}</td>
          <td style="padding:7px 10px;color:#555">{up_str}</td>
          <td style="padding:7px 10px;color:#555;font-weight:600">{starts}{ends}</td>
          <td style="padding:7px 10px">{link}</td>
        </tr>"""

    return f"""
  <h3 style="margin-top:40px">📅 Upcoming Sales</h3>
  <p style="color:#666;font-size:12px;margin-top:-6px">
    These deals are in current flyers but haven't started yet.
    Add them to your shopping plan.
  </p>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#546e7a;color:white;font-size:12px">
        <th style="padding:8px 10px;text-align:left">Product</th>
        <th style="padding:8px 10px;text-align:left">Store</th>
        <th style="padding:8px 10px;text-align:left">Pack</th>
        <th style="padding:8px 10px;text-align:left">Sale Price</th>
        <th style="padding:8px 10px;text-align:left">Unit Price</th>
        <th style="padding:8px 10px;text-align:left">Sale Dates</th>
        <th style="padding:8px 10px;text-align:left"></th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>"""


def _render_comparisons(comparisons: list[ComparisonRow]) -> str:
    if not comparisons:
        return ""

    by_cat: dict[str, list[ComparisonRow]] = defaultdict(list)
    for row in comparisons:
        by_cat[row.category].append(row)

    all_stores = sorted({s for row in comparisons for s in row.unit_prices})
    store_th   = "".join(
        f"<th style='padding:8px;text-align:right'>{s}</th>" for s in all_stores
    )

    tbody = ""
    for cat in sorted(by_cat.keys()):
        tbody += f"""
        <tr>
          <td colspan="{len(all_stores) + 4}"
              style="background:#f5f5f5;font-weight:600;padding:8px 10px;
                     text-transform:uppercase;font-size:11px;letter-spacing:.5px;
                     color:#555;border-top:2px solid #ddd">
            {cat}
          </td>
        </tr>"""

        for row in sorted(by_cat[cat], key=lambda r: r.unit_savings, reverse=True):
            cells = ""
            for store in all_stores:
                up = row.unit_prices.get(store)
                pi = row.pack_info.get(store, "")
                st = row.stats.get(store)

                if up is None:
                    cells += "<td style='padding:7px 8px;text-align:right;color:#ccc'>—</td>"
                    continue

                # trend badge
                trend_html = ""
                if st and st.price_trend:
                    arrows = {"rising": "↑", "falling": "↓", "stable": "→"}
                    colors = {"rising": "#f44336", "falling": "#4caf50", "stable": "#9e9e9e"}
                    arrow  = arrows[st.price_trend]
                    color  = colors[st.price_trend]
                    trend_html = f"<span style='color:{color};font-size:10px'> {arrow}</span>"

                # all-time-low star
                low_html = ""
                if st and st.is_all_time_low:
                    low_html = "<span style='color:#ff9800;font-size:10px'> ★</span>"

                if store == row.best_store:
                    cell_style = (
                        "padding:7px 8px;text-align:right;"
                        "background:#e8f5e9;font-weight:700;color:#2e7d32"
                    )
                else:
                    cell_style = "padding:7px 8px;text-align:right"

                tooltip = pi.replace('"', '&quot;')
                cells += (
                    f"<td style='{cell_style}' title='{tooltip}'>"
                    f"${up:.3f}{trend_html}{low_html}</td>"
                )

            base_unit = row.base_unit or ""
            savings   = (
                f"<span style='color:#2e7d32;font-size:12px'>"
                f"save ${row.unit_savings:.3f}/{base_unit} ({row.pct_savings:.0f}%)</span>"
                if row.unit_savings > 0 else ""
            )

            tbody += f"""
            <tr style="border-bottom:1px solid #f0f0f0;font-size:13px">
              <td style="padding:7px 10px;font-weight:500">{row.canonical_name}</td>
              <td style="padding:7px 10px;color:#888;font-size:11px">per {base_unit}</td>
              {cells}
              <td style="padding:7px 10px">{savings}</td>
            </tr>"""

    return f"""
  <h3 style="margin-top:40px">📊 Cross-Store Price Comparison</h3>
  <p style="color:#666;font-size:12px;margin-top:-6px">
    All prices shown as <strong>unit price</strong> ($/roll, $/oz, etc.) for fair comparison across pack sizes.
    Green = cheapest · ↑ rising · ↓ falling · ★ all-time low · hover for pack details.
  </p>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#444;color:white;font-size:12px">
        <th style="padding:8px 10px;text-align:left">Product</th>
        <th style="padding:8px 10px;text-align:left">Unit</th>
        {store_th}
        <th style="padding:8px 10px;text-align:left">Savings vs Worst</th>
      </tr>
    </thead>
    <tbody>{tbody}</tbody>
  </table>"""


# ── Send ──────────────────────────────────────────────────────────────────────

def send_digest(digest: Digest):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_addr   = os.environ["NOTIFY_EMAIL"]

    subject = (
        f"🛒 {digest.total_deals} deals + {len(digest.comparisons)} price comparisons "
        f"({digest.generated_at.strftime('%b %d')})"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = to_addr

    plain = "\n".join(
        f"{e.category.upper()} | {e.product_name} @ {e.store} "
        f"${e.pack_price:.2f}"
        + (f" (${e.unit_price:.3f}/{e.base_unit})" if e.unit_price else "")
        for e in digest.entries
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(_render_html(digest), "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_addr, msg.as_string())

    print(f"[notifier] digest sent → {to_addr} "
          f"({digest.total_deals} deals, {len(digest.comparisons)} comparisons)")
