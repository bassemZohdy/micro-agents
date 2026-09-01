"""Micro-Agent Memory.

Memory represents information retained across interactions.
Memory != Session. Memory != Knowledge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from time import monotonic
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

    def __post_init__(self) -> None:
        """Reject values that would make retention or eviction ambiguous."""
        if not isinstance(self.auto_store, bool):
            raise ValueError("auto_store must be a boolean")
        if self.max_entries is not None and (
            isinstance(self.max_entries, bool)
            or not isinstance(self.max_entries, int)
            or self.max_entries < 1
        ):
            raise ValueError("max_entries must be a positive integer")
        if self.ttl_seconds is not None and (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, int)
            or self.ttl_seconds < 0
        ):
            raise ValueError("ttl_seconds must be a non-negative integer")


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
    """In-memory memory provider for development and testing.

    Enforces an optional MemoryPolicy: max_entries evicts the
    least-recently-stored entry, ttl_seconds expires entries on read,
    and auto_store is exposed for callers deciding whether to persist
    interactions automatically.
    """

    def __init__(self, policy: MemoryPolicy | None = None) -> None:
        self.policy = policy or MemoryPolicy()
        self._entries: dict[str, MemoryEntry] = {}
        self._stored_at: dict[str, float] = {}

    def _key(self, key: str, scope: str | None) -> str:
        # Default scope matches MemoryEntry.scope's default so that
        # store(MemoryEntry(key=...)) / get(key=...) round-trip.
        return f"{scope or 'agent'}:{key}"

    def _is_expired(self, k: str, now: float | None = None) -> bool:
        if self.policy.ttl_seconds is None:
            return False
        stored = self._stored_at.get(k)
        if stored is None:
            return False
        reference = monotonic() if now is None else now
        return reference - stored >= self.policy.ttl_seconds

    def _purge_expired(self, now: float | None = None) -> None:
        """Remove expired entries before reads, writes, and capacity checks."""
        if self.policy.ttl_seconds is None:
            return
        reference = monotonic() if now is None else now
        for key in tuple(self._entries):
            if self._is_expired(key, reference):
                self._entries.pop(key, None)
                self._stored_at.pop(key, None)

    def _evict_if_full(self) -> None:
        if self.policy.max_entries is None:
            return
        self._purge_expired()
        while len(self._entries) >= self.policy.max_entries:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
            self._stored_at.pop(oldest, None)

    async def store(self, entry: MemoryEntry) -> None:
        self._purge_expired()
        k = self._key(entry.key, entry.scope)
        # Re-storing an existing key must not evict itself.
        if k not in self._entries:
            self._evict_if_full()
        self._entries[k] = entry
        self._stored_at[k] = monotonic()

    async def search(
        self, query: str, scope: str | None = None, limit: int = 10
    ) -> list[MemoryEntry]:
        self._purge_expired()
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
        self._purge_expired()
        k = self._key(key, scope)
        return self._entries.get(k)

    async def delete(self, key: str, scope: str | None = None) -> bool:
        self._purge_expired()
        k = self._key(key, scope)
        if k in self._entries:
            del self._entries[k]
            self._stored_at.pop(k, None)
            return True
        return False

    async def list_entries(self, scope: str | None = None) -> list[MemoryEntry]:
        self._purge_expired()
        results = []
        for entry in self._entries.values():
            if scope is None or entry.scope == scope:
                results.append(entry)
        return results
