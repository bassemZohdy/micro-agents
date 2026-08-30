"""Micro-Agent Memory.

Memory represents information retained across interactions.
Memory != Session. Memory != Knowledge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Memory Model
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single memory entry."""

    key: str
    value: Any
    scope: str = "agent"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryPolicy:
    """Policy governing memory behavior."""

    auto_store: bool = False
    max_entries: int | None = None
    ttl_seconds: int | None = None


# ---------------------------------------------------------------------------
# Memory Provider Interface
# ---------------------------------------------------------------------------


class MemoryProvider(ABC):
    """Abstract memory provider interface."""

    @abstractmethod
    async def store(self, entry: MemoryEntry) -> None:
        """Store a memory entry."""

    @abstractmethod
    async def search(
        self, query: str, scope: str | None = None, limit: int = 10
    ) -> list[MemoryEntry]:
        """Search memory entries."""

    @abstractmethod
    async def get(self, key: str, scope: str | None = None) -> MemoryEntry | None:
        """Get a specific memory entry by key."""

    @abstractmethod
    async def delete(self, key: str, scope: str | None = None) -> bool:
        """Delete a memory entry. Returns True if deleted."""

    @abstractmethod
    async def list_entries(self, scope: str | None = None) -> list[MemoryEntry]:
        """List all memory entries, optionally filtered by scope."""


# ---------------------------------------------------------------------------
# In-Memory Memory Provider
# ---------------------------------------------------------------------------


class InMemoryMemoryProvider(MemoryProvider):
    """In-memory memory provider for development and testing."""

    def __init__(self) -> None:
        self._entries: dict[str, MemoryEntry] = {}

    def _key(self, key: str, scope: str | None) -> str:
        return f"{scope or 'default'}:{key}"

    async def store(self, entry: MemoryEntry) -> None:
        self._entries[self._key(entry.key, entry.scope)] = entry

    async def search(
        self, query: str, scope: str | None = None, limit: int = 10
    ) -> list[MemoryEntry]:
        results = []
        for entry in self._entries.values():
            if scope and entry.scope != scope:
                continue
            if query.lower() in str(entry.value).lower():
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    async def get(self, key: str, scope: str | None = None) -> MemoryEntry | None:
        return self._entries.get(self._key(key, scope))

    async def delete(self, key: str, scope: str | None = None) -> bool:
        k = self._key(key, scope)
        if k in self._entries:
            del self._entries[k]
            return True
        return False

    async def list_entries(self, scope: str | None = None) -> list[MemoryEntry]:
        if scope is None:
            return list(self._entries.values())
        return [e for e in self._entries.values() if e.scope == scope]
