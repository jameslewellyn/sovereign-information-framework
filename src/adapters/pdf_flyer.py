"""
PDF / image flyer adapter.
Downloads a flyer PDF or image and parses it with a vision LLM.
"""

from __future__ import annotations

import pathlib
import tempfile

import httpx

from src.adapters.base import SourceAdapter
from src.models import AdItem
from src.parsers.llm_parser import LLMParser


class PDFFlyerAdapter(SourceAdapter):
    name = "pdf_flyer"

    async def fetch(self) -> list[AdItem]:
        url      = self.config["flyer_url"]
        store    = self.config["store"]
        model    = self.config.get("llm_model", "llama3.2-vision")

        with tempfile.TemporaryDirectory() as tmp:
            flyer_path = pathlib.Path(tmp) / "flyer"

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                flyer_path.write_bytes(resp.content)

            parser = LLMParser(model=model, store=store, source=self.name)
            items  = await parser.parse_file(str(flyer_path))

        return items
