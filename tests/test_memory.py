"""Tests for Micro-Agent Memory."""

import pytest

from micro_agent.memory import (
    InMemoryMemoryProvider,
    MemoryEntry,
    MemoryPolicy,
    MemoryProvider,
    RedisMemoryProvider,
    StateConflictError,
)
from tests.fake_redis import FakeRedis, FakeRedisBackend


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

    @pytest.mark.asyncio
    async def test_tenant_isolation_and_version_conflict(self):
        provider = InMemoryMemoryProvider()
        await provider.store(MemoryEntry(key="pref", value="a", tenant_id="tenant-a"))
        await provider.store(MemoryEntry(key="pref", value="b", tenant_id="tenant-b"))
        assert await provider.get("pref") is None
        stale = await provider.get("pref", tenant_id="tenant-a")
        current = await provider.get("pref", tenant_id="tenant-a")
        current.value = "updated"
        await provider.store(current)
        assert current.version == 2
        with pytest.raises(StateConflictError, match="version conflict"):
            await provider.store(stale)


class TestRedisMemoryProvider:
    """Redis memory shares scoped entries and retention policy across clients."""

    @pytest.mark.asyncio
    async def test_replicas_share_scoped_entries(self):
        backend = FakeRedisBackend()
        replica_a = RedisMemoryProvider(client=FakeRedis(backend))
        replica_b = RedisMemoryProvider(client=FakeRedis(backend))

        await replica_a.store(MemoryEntry(key="pref", value="dark_mode", scope="user"))
        await replica_a.store(MemoryEntry(key="rule", value="agent-only", scope="agent"))

        assert (await replica_b.get("pref", scope="user")).value == "dark_mode"
        assert await replica_b.get("pref", scope="agent") is None
        assert len(await replica_b.search("mode", scope="user")) == 1
        assert len(await replica_b.list_entries(scope="agent")) == 1

    @pytest.mark.asyncio
    async def test_tenant_isolation_and_version_conflict(self):
        backend = FakeRedisBackend()
        provider = RedisMemoryProvider(client=FakeRedis(backend))
        await provider.store(MemoryEntry(key="pref", value="a", tenant_id="tenant-a"))
        await provider.store(MemoryEntry(key="pref", value="b", tenant_id="tenant-b"))
        assert await provider.get("pref") is None
        stale = await provider.get("pref", tenant_id="tenant-a")
        current = await provider.get("pref", tenant_id="tenant-a")
        current.value = "updated"
        await provider.store(current)
        assert current.version == 2
        with pytest.raises(StateConflictError, match="version conflict"):
            await provider.store(stale)

    @pytest.mark.asyncio
    async def test_capacity_evicts_oldest_and_ttl_zero_is_not_retained(self):
        backend = FakeRedisBackend()
        provider = RedisMemoryProvider(
            client=FakeRedis(backend),
            policy=MemoryPolicy(max_entries=2),
        )
        await provider.store(MemoryEntry(key="a", value="one"))
        await provider.store(MemoryEntry(key="b", value="two"))
        await provider.store(MemoryEntry(key="c", value="three"))
        assert await provider.get("a") is None
        assert await provider.get("b") is not None
        assert await provider.get("c") is not None

        expiring = RedisMemoryProvider(
            client=FakeRedis(backend),
            policy=MemoryPolicy(ttl_seconds=0),
        )
        await expiring.store(MemoryEntry(key="expired", value="old"))
        assert await expiring.get("expired") is None
        assert all(entry.key != "expired" for entry in await expiring.list_entries())

    def test_endpoint_must_be_redis_url(self):
        with pytest.raises(ValueError, match="Redis session endpoint"):
            RedisMemoryProvider("https://memory.example.test", client=FakeRedis(FakeRedisBackend()))
