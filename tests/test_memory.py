"""Tests for Micro-Agent Memory."""

import pytest

from micro_agent.memory import (
    InMemoryMemoryProvider,
    MemoryEntry,
    MemoryPolicy,
    MemoryProvider,
)


class TestMemoryEntry:
    """Test memory entry."""

    def test_basic_entry(self):
        entry = MemoryEntry(key="user_pref", value="dark_mode")
        assert entry.key == "user_pref"
        assert entry.scope == "agent"

    def test_entry_with_scope(self):
        entry = MemoryEntry(key="pref", value="light", scope="user")
        assert entry.scope == "user"


class TestMemoryPolicy:
    """Test memory policy."""

    def test_defaults(self):
        policy = MemoryPolicy()
        assert policy.auto_store is False
        assert policy.max_entries is None


class TestMemoryProviderInterface:
    """Test that MemoryProvider is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            MemoryProvider()  # type: ignore[abstract]


class TestInMemoryMemoryProvider:
    """Test in-memory memory provider."""

    @pytest.mark.asyncio
    async def test_store_and_get(self):
        provider = InMemoryMemoryProvider()
        entry = MemoryEntry(key="pref", value="dark_mode", scope="user")
        await provider.store(entry)
        retrieved = await provider.get("pref", scope="user")
        assert retrieved is not None
        assert retrieved.value == "dark_mode"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        provider = InMemoryMemoryProvider()
        assert await provider.get("missing") is None

    @pytest.mark.asyncio
    async def test_search(self):
        provider = InMemoryMemoryProvider()
        await provider.store(MemoryEntry(key="a", value="hello world"))
        await provider.store(MemoryEntry(key="b", value="goodbye world"))
        await provider.store(MemoryEntry(key="c", value="hello there"))
        results = await provider.search("hello")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_with_scope(self):
        provider = InMemoryMemoryProvider()
        await provider.store(MemoryEntry(key="a", value="test", scope="user"))
        await provider.store(MemoryEntry(key="b", value="test", scope="agent"))
        results = await provider.search("test", scope="user")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_with_limit(self):
        provider = InMemoryMemoryProvider()
        for i in range(10):
            await provider.store(MemoryEntry(key=f"k{i}", value="match"))
        results = await provider.search("match", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_delete(self):
        provider = InMemoryMemoryProvider()
        await provider.store(MemoryEntry(key="a", value="val", scope="agent"))
        assert await provider.delete("a", scope="agent") is True
        assert await provider.get("a", scope="agent") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        provider = InMemoryMemoryProvider()
        assert await provider.delete("missing") is False

    @pytest.mark.asyncio
    async def test_list_entries(self):
        provider = InMemoryMemoryProvider()
        await provider.store(MemoryEntry(key="a", value="1", scope="user"))
        await provider.store(MemoryEntry(key="b", value="2", scope="agent"))
        all_entries = await provider.list_entries()
        assert len(all_entries) == 2
        user_entries = await provider.list_entries(scope="user")
        assert len(user_entries) == 1
