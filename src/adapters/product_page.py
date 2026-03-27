"""
ProductPageAdapter — scrapes individual product pages for everyday prices.

Driven entirely by conf/products/catalog.yml. For each product+store entry
in the catalog it fetches the page, extracts the current shelf price using
an LLM (via Instructor), and returns AdItems with price_type="everyday".

This runs on a separate (slower) schedule from flyer adapters since product
page prices change less frequently than weekly flyers.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from src.adapters.base import SourceAdapter
from src.models import AdItem


# ── LLM extraction schema ─────────────────────────────────────────────────

class _ExtractedPrice(BaseModel):
    price:        float
    unit:         Optional[str] = None   # e.g. "each", "per lb", "30-pack"
    product_name: Optional[str] = None
    in_stock:     bool = True


# ── Store-specific CSS hints (speeds up extraction, reduces token usage) ──
# Keys match store names in the catalog. Each entry is a CSS selector that
# wraps the price on that store's product pages. Used to pre-filter HTML
# before sending to the LLM. Falls back to full-page extraction if absent.

STORE_PRICE_SELECTORS: dict[str, str] = {
    "Costco":     "[automation='product-price'], .price",
    "Walmart":    "[itemprop='price'], [data-testid='price-wrap']",
    "Target":     "[data-test='product-price'], .ProductPricingWrapper",
    "Sam's Club": ".ProductPricing, [class*='price']",
    "Aldi":       ".price, .product-tile__price",
    "Kroger":     "[data-testid='price'], .kds-Price",
    "Walgreens":  ".product-price, [data-testid='productprice']",
}


class ProductPageAdapter(SourceAdapter):
    """
    Reads conf/products/catalog.yml and scrapes one URL per (product, store).
    Returns AdItems with price_type="everyday".
    """

    name = "product_page"

    async def fetch(self) -> list[AdItem]:
        catalog   = self.config.get("catalog", {})
        llm_model = self.config.get("llm_model", "ollama/llama3.2")
        client    = self._build_instructor(llm_model)

        tasks = []
        for cat_slug, cat in catalog.get("categories", {}).items():
            cat_label = cat.get("label", cat_slug)
            for prod_slug, prod in cat.get("products", {}).items():
                product_key = f"{cat_slug}/{prod_slug}"
                for store, url in prod.get("stores", {}).items():
                    tasks.append(self._scrape_one(
                        client      = client,
                        llm_model   = llm_model,
                        store       = store,
                        product_key = product_key,
                        product_label = prod["label"],
                        unit        = prod.get("unit"),
                        category    = cat_label,
                        url         = url,
                    ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        items   = []
        for r in results:
            if isinstance(r, Exception):
                print(f"[product_page] fetch error: {r}")
            elif r is not None:
                items.append(r)
        return items

    async def _scrape_one(
        self,
        client,
        llm_model: str,
        store: str,
        product_key: str,
        product_label: str,
        unit: Optional[str],
        category: str,
        url: str,
    ) -> AdItem | None:
        html = await self._fetch_html(url)
        if not html:
            return None

        # narrow HTML to price area using store-specific selector if available
        price_html = self._extract_price_region(html, store)

        extracted = await self._llm_extract(client, llm_model, price_html, product_label, url)
        if not extracted or not extracted.in_stock:
            return None

        return AdItem(
            store        = store,
            source       = self.name,
            product_name = extracted.product_name or product_label,
            category     = category,
            sale_price   = extracted.price,
            unit         = extracted.unit or unit,
            price_type   = "everyday",
            source_url   = url,
        )

    async def _fetch_html(self, url: str) -> str | None:
        """Fetch page HTML using httpx with browser-like headers."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.text
                print(f"[product_page] HTTP {resp.status_code} for {url}")
        except Exception as e:
            print(f"[product_page] request error for {url}: {e}")
        return None

    def _extract_price_region(self, html: str, store: str) -> str:
        """
        Attempt to find the price container using BeautifulSoup + store hint.
        Falls back to truncated raw HTML if selector misses.
        """
        selector = STORE_PRICE_SELECTORS.get(store)
        if selector:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                # try each comma-separated selector
                for sel in selector.split(","):
                    el = soup.select_one(sel.strip())
                    if el:
                        return el.get_text(separator=" ", strip=True)[:500]
            except Exception:
                pass
        # fallback: strip scripts/styles, return first 3000 chars
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(separator=" ", strip=True)[:3000]
        except Exception:
            return html[:3000]

    async def _llm_extract(
        self,
        client,
        model: str,
        text: str,
        product_label: str,
        url: str,
    ) -> _ExtractedPrice | None:
        instructor_client, model_name = client
        try:
            return await instructor_client.chat.completions.create(
                model          = model_name,
                response_model = _ExtractedPrice,
                messages       = [{
                    "role":    "user",
                    "content": (
                        f"Extract the current retail price for '{product_label}' "
                        f"from this product page text. Return the price as a float, "
                        f"the unit/size description, and whether the item is in stock.\n\n"
                        f"{text}"
                    ),
                }],
                max_retries=2,
            )
        except Exception as e:
            print(f"[product_page] LLM extraction failed for {url}: {e}")
            return None

    def _build_instructor(self, model: str):
        import instructor
        provider, model_name = model.split("/", 1)
        if provider == "ollama":
            import ollama
            return instructor.from_ollama(ollama.AsyncClient()), model_name
        elif provider == "openai":
            from openai import AsyncOpenAI
            return instructor.from_openai(AsyncOpenAI()), model_name
        elif provider == "anthropic":
            import anthropic
            return instructor.from_anthropic(anthropic.AsyncAnthropic()), model_name
        raise ValueError(f"Unsupported provider: {provider!r}")
