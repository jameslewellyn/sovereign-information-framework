"""
Base adapter interface. Every store source implements this.

To add a new store:
  1. Create src/adapters/mystore.py
  2. Subclass SourceAdapter
  3. Implement fetch() — return a list[AdItem]
  4. Add an entry in conf/adapters/mystore.yml
  5. Register it in src/adapters/__init__.py

That's it. The scheduler, matcher, and notifier need no changes.
"""

from __future__ import annotations

import abc
from typing import Any
from src.models import AdItem


class SourceAdapter(abc.ABC):
    """
    Abstract base for all store/source adapters.

    Each adapter is responsible for:
    - Fetching raw data from one source (API, scraper, PDF, etc.)
    - Normalising it into a list[AdItem]

    Adapters should be stateless. All config is passed via __init__.
    """

    #: unique slug used in AdItem.source and config filenames
    name: str = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abc.abstractmethod
    async def fetch(self) -> list[AdItem]:
        """
        Fetch and return all current deals from this source.
        Must be idempotent — safe to call multiple times.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
