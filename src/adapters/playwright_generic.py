"""
Generic Playwright adapter.
Works for any store website by combining browser scraping with
a pluggable parse strategy (CSS-only, Crawl4AI+LLM, or Ollama vision).

Configure per store via conf/adapters/<store>.yml.
"""

from __future__ import annotations

from typing import Any

from src.adapters.base import SourceAdapter
from src.models import AdItem
from src.parsers.css_parser import CSSParser
from src.parsers.llm_parser import LLMParser


class PlaywrightGenericAdapter(SourceAdapter):
    name = "playwright_generic"

    async def fetch(self) -> list[AdItem]:
        from playwright.async_api import async_playwright

        url       = self.config["url"]
        store     = self.config["store"]
        strategy  = self.config.get("parse_strategy", "css_only")
        headless  = self.config.get("headless", True)
        wait_for  = self.config.get("wait_for", "networkidle")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            page    = await browser.new_page()
            await page.goto(url, wait_until=wait_for, timeout=60_000)

            if strategy == "css_only":
                raw = await self._extract_css(page)
                items = CSSParser(self.config["selectors"], store, self.name).parse(raw)

            elif strategy in ("crawl4ai_llm", "ollama_vision"):
                html  = await page.content()
                items = await LLMParser(
                    model   = self.config.get("llm_model", "ollama/llama3.2"),
                    store   = store,
                    source  = self.name,
                ).parse_html(html)

            else:
                raise ValueError(f"Unknown parse_strategy: {strategy!r}")

            await browser.close()

        return items

    async def _extract_css(self, page) -> list[dict[str, Any]]:
        """Extract raw dicts using CSS selectors from config."""
        sel = self.config["selectors"]
        return await page.evaluate(f"""() => {{
            return [...document.querySelectorAll('{sel["item_container"]}')]
                .map(el => ({{
                    name:           el.querySelector('{sel["name"]}')?.innerText?.trim(),
                    sale_price:     el.querySelector('{sel["sale_price"]}')?.innerText?.trim(),
                    original_price: el.querySelector('{sel.get("original_price", ".__none__")}')?.innerText?.trim(),
                    valid_until:    el.querySelector('{sel.get("valid_until",    ".__none__")}')?.innerText?.trim(),
                    image_url:      el.querySelector('img')?.src,
                }}))
                .filter(i => i.name && i.sale_price);
        }}""")
