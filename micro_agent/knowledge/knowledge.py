"""Micro-Agent Knowledge.

Knowledge represents externally supplied domain information.
Knowledge != Memory. Knowledge != Session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Knowledge Model
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeSource:
    """A reference to an external knowledge source."""

    ref: str
    source_type: str | None = None
    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeEntry:
    """A retrieved knowledge entry."""

    content: str
    source_ref: str = ""
    relevance: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Knowledge Retriever Interface
# ---------------------------------------------------------------------------


class KnowledgeRetriever(ABC):
    """Abstract knowledge retriever interface."""

    @abstractmethod
    async def retrieve(
        self, query: str, source: KnowledgeSource, limit: int = 5
    ) -> list[KnowledgeEntry]:
        """Retrieve knowledge entries from a source."""

    @abstractmethod
    async def health_check(self, source: KnowledgeSource) -> bool:
        """Check if a knowledge source is available."""
