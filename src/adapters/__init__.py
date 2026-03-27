"""
Adapter registry — maps config adapter: name → class.

Imports are lazy so that optional heavy dependencies (playwright, instructor,
anthropic, etc.) don't prevent entry-point scripts from starting when those
libraries aren't installed.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.adapters.base import SourceAdapter


def _load_class(module_path: str, class_name: str):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


_REGISTRY_MAP = {
    "flipp":              ("src.adapters.flipp",              "FlippAdapter"),
    "playwright_generic": ("src.adapters.playwright_generic", "PlaywrightGenericAdapter"),
    "pdf_flyer":          ("src.adapters.pdf_flyer",          "PDFFlyerAdapter"),
    "product_page":       ("src.adapters.product_page",       "ProductPageAdapter"),
}


def load_adapter(config: dict) -> "SourceAdapter":
    """Instantiate the correct adapter from a config dict."""
    key = config.get("adapter")
    entry = _REGISTRY_MAP.get(key)
    if not entry:
        raise ValueError(f"Unknown adapter: {key!r}. Available: {list(_REGISTRY_MAP)}")
    cls = _load_class(*entry)
    return cls(config)
