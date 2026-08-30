"""Micro-Agent Knowledge.

Knowledge represents externally supplied domain information.
Knowledge != Memory. Knowledge != Session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from hashlib import sha256
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


def compute_content_hash(content: str) -> str:
    """Content hash (sha256 hex digest) for integrity metadata."""
    return sha256(content.encode("utf-8")).hexdigest()


class InMemoryKnowledgeRetriever(KnowledgeRetriever):
    """In-memory knowledge retriever for development and testing.

    Documents are supplied per source ref. Retrieved entries carry integrity
    metadata: a content hash and the source version, so consumers can verify
    what they retrieved.
    """

    def __init__(self, documents: dict[str, list[str | dict[str, Any]]] | None = None) -> None:
        # ref -> list of str content or {"content": ..., **metadata} dicts
        self._documents: dict[str, list[dict[str, Any]]] = {}
        for ref, docs in (documents or {}).items():
            self._documents[ref] = [
                {"content": doc} if isinstance(doc, str) else dict(doc) for doc in docs
            ]

    def add_document(self, source: KnowledgeSource, content: str, **metadata: Any) -> None:
        self._documents.setdefault(source.ref, []).append(
            {"content": content, "version": source.version, **metadata}
        )

    async def retrieve(
        self, query: str, source: KnowledgeSource, limit: int = 5
    ) -> list[KnowledgeEntry]:
        docs = self._documents.get(source.ref, [])
        terms = query.lower().split()
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in docs:
            content = str(doc.get("content", ""))
            text = content.lower()
            matches = sum(1 for term in terms if term in text)
            if terms and matches == 0:
                continue
            relevance = matches / len(terms) if terms else 1.0
            scored.append((relevance, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        entries = []
        for relevance, doc in scored[:limit]:
            content = str(doc.get("content", ""))
            entries.append(
                KnowledgeEntry(
                    content=content,
                    source_ref=source.ref,
                    relevance=round(relevance, 3),
                    metadata={
                        "content_hash": compute_content_hash(content),
                        "version": doc.get("version", source.version),
                        **{k: v for k, v in doc.items() if k not in ("content", "version")},
                    },
                )
            )
        return entries

    async def health_check(self, source: KnowledgeSource) -> bool:
        return source.ref in self._documents
