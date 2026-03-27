"""
LLM-based parser using Instructor for guaranteed structured output.
Supports any model provider that Instructor supports.
"""

from __future__ import annotations

import base64
import pathlib
from typing import Optional

import instructor
from pydantic import BaseModel

from src.models import AdItem


class _RawItem(BaseModel):
    product_name:   str
    brand:          Optional[str]  = None
    category:       Optional[str]  = None
    sale_price:     float
    original_price: Optional[float] = None
    unit:           Optional[str]  = None
    valid_until:    Optional[str]  = None


class _RawFlyer(BaseModel):
    store_name: str
    items:      list[_RawItem]


class LLMParser:
    """
    Parses HTML text or image files into AdItems via an LLM.

    model format:  "ollama/llama3.2"  |  "openai/gpt-4o"  |  "anthropic/claude-3-5-sonnet"
    """

    PROMPT = (
        "Extract every sale item from the following store flyer content. "
        "Include product name, brand (if visible), category, sale price, "
        "original price (if shown), unit (e.g. per lb, each), "
        "and offer expiry date. Return all items you can find."
    )

    def __init__(self, model: str, store: str, source: str):
        self.model  = model
        self.store  = store
        self.source = source
        self._client = self._build_client(model)

    def _build_client(self, model: str):
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
        elif provider == "google":
            import google.generativeai as genai
            return instructor.from_gemini(genai.GenerativeModel(model_name)), model_name
        raise ValueError(f"Unsupported provider: {provider!r}")

    async def parse_html(self, html: str) -> list[AdItem]:
        client, model_name = self._client
        result: _RawFlyer = await client.chat.completions.create(
            model          = model_name,
            response_model = _RawFlyer,
            messages       = [{"role": "user", "content": f"{self.PROMPT}\n\n{html[:12000]}"}],
        )
        return self._to_items(result)

    async def parse_file(self, path: str) -> list[AdItem]:
        """Parse a PDF or image file. Converts PDF pages to images first."""
        suffix = pathlib.Path(path).suffix.lower()

        if suffix == ".pdf":
            from pdf2image import convert_from_path
            pages = convert_from_path(path)
        else:
            from PIL import Image
            pages = [Image.open(path)]

        items: list[AdItem] = []
        for page in pages:
            tmp = "/tmp/_flyer_page.png"
            page.save(tmp)
            items.extend(await self._parse_image(tmp))
        return items

    async def _parse_image(self, image_path: str) -> list[AdItem]:
        client, model_name = self._client
        img_b64 = base64.b64encode(pathlib.Path(image_path).read_bytes()).decode()

        result: _RawFlyer = await client.chat.completions.create(
            model          = model_name,
            response_model = _RawFlyer,
            messages       = [{
                "role":    "user",
                "content": self.PROMPT,
                "images":  [img_b64],
            }],
        )
        return self._to_items(result)

    def _to_items(self, raw: _RawFlyer) -> list[AdItem]:
        items = []
        for r in raw.items:
            discount = None
            if r.original_price and r.original_price > r.sale_price:
                discount = round((r.original_price - r.sale_price) / r.original_price * 100, 1)
            items.append(AdItem(
                store          = self.store,
                source         = self.source,
                product_name   = r.product_name,
                brand          = r.brand,
                category       = r.category,
                sale_price     = r.sale_price,
                original_price = r.original_price,
                discount_pct   = discount,
                unit           = r.unit,
                valid_until    = r.valid_until,
            ))
        return items
