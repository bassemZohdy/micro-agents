"""Edge cases for the Redis memory provider using the shared fake client."""

import json

import pytest

from micro_agent.memory import MemoryEntry, MemoryPolicy, RedisMemoryProvider
from micro_agent.state import StateConflictError
from tests.fake_redis import FakeRedis, FakeRedisBackend

ENDPOINT = "redis://redis.example.test/0"


def _provider(backend: FakeRedisBackend | None = None, **kwargs) -> RedisMemoryProvider:
    backend = backend or FakeRedisBackend()
    return RedisMemoryProvider(ENDPOINT, namespace="mem-test", client=FakeRedis(backend), **kwargs)


def test_constructor_validation():
    for bad in ("", " padded ", " leading", "trailing "):
        with pytest.raises(ValueError, match="namespace"):
            RedisMemoryProvider(ENDPOINT, namespace=bad, client=FakeRedis(FakeRedisBackend()))
    with pytest.raises(ValueError):
        RedisMemoryProvider("http://not-redis", client=FakeRedis(FakeRedisBackend()))


@pytest.mark.asyncio
async def test_store_get_search_delete_roundtrip_and_default_scope():
    provider = _provider()
    await provider.store(MemoryEntry(key="a", value="Hello World", scope="s1"))
    await provider.store(MemoryEntry(key="b", value="other", scope="s2", metadata={"k": 1}))

    entry = await provider.get("a", "s1")
    assert entry is not None and entry.version == 1
    assert "stored_at" in entry.metadata
    assert await provider.get("missing", "s1") is None

    assert [e.key for e in await provider.search("hello")] == ["a"]
    assert await provider.search("hello", limit=0) == []
    assert [e.key for e in await provider.list_entries(scope="s2")] == ["b"]

    assert await provider.delete("a", "s1") is True
    assert await provider.delete("a", "s1") is False
    assert await provider.get("a", "s1") is None
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_versions_increment_and_conflicts_are_detected():
    provider = _provider()
    await provider.store(MemoryEntry(key="k", value=1))
    await provider.store(MemoryEntry(key="k", value=2), expected_version=1)
    current = await provider.get("k")
    assert current is not None and current.version == 2
    with pytest.raises(StateConflictError):
        await provider.store(MemoryEntry(key="k", value=3), expected_version=1)
    with pytest.raises(StateConflictError):
        await provider.store(MemoryEntry(key="new", value=1), expected_version=5)


@pytest.mark.asyncio
async def test_tenants_are_isolated():
    provider = _provider()
    await provider.store(MemoryEntry(key="k", value="a", tenant_id="t/1"))
    await provider.store(MemoryEntry(key="k", value="b", tenant_id="t2"))
    assert (await provider.get("k", tenant_id="t/1")).value == "a"  # type: ignore[union-attr]
    assert (await provider.get("k", tenant_id="t2")).value == "b"  # type: ignore[union-attr]
    assert await provider.get("k") is None
    assert [e.value for e in await provider.list_entries(tenant_id="t2")] == ["b"]


@pytest.mark.asyncio
async def test_corrupt_and_orphaned_records_are_purged_on_read():
    backend = FakeRedisBackend()
    provider = _provider(backend)
    await provider.store(MemoryEntry(key="good", value="ok"))
    index = "mem-test:memory:index"
    good_key = next(iter(backend.index[index]))

    for name, raw in {
        "bad-json": "{not json",
        "not-object": "[1]",
        "no-scope": '{"key": "x"}',
    }.items():
        key = f"mem-test:memory:agent:{name}"
        backend.values[key] = raw
        backend.index[index][key] = 1.0
    orphan = "mem-test:memory:agent:orphan"
    backend.index[index][orphan] = 1.0  # index member whose value is gone

    entries = await provider.list_entries()
    assert [e.key for e in entries] == ["good"]
    assert list(backend.index[index]) == [good_key]
    assert not any(k.endswith(("bad-json", "not-object", "no-scope")) for k in backend.values)

    backend.values["mem-test:memory:agent:rotten"] = "{not json"
    backend.index[index]["mem-test:memory:agent:rotten"] = 2.0
    assert await provider.get("rotten") is None
    assert "mem-test:memory:agent:rotten" not in backend.values
    # A key that is simply absent also scrubs its stale index member.
    backend.index[index]["mem-test:memory:agent:ghost"] = 3.0
    assert await provider.get("ghost") is None
    assert "mem-test:memory:agent:ghost" not in backend.index[index]


@pytest.mark.asyncio
async def test_expired_records_are_removed_on_get():
    backend = FakeRedisBackend()
    provider = _provider(backend, policy=MemoryPolicy(ttl_seconds=60))
    await provider.store(MemoryEntry(key="k", value="v"))
    key = next(iter(backend.values))
    payload = json.loads(backend.values[key])
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    backend.values[key] = json.dumps(payload)

    assert await provider.get("k") is None
    assert key not in backend.values


@pytest.mark.asyncio
async def test_capacity_eviction_purges_stale_entries_first_then_oldest():
    backend = FakeRedisBackend()
    provider = _provider(backend, policy=MemoryPolicy(max_entries=2))
    index = "mem-test:memory:index"

    await provider.store(MemoryEntry(key="a", value=1))
    await provider.store(MemoryEntry(key="b", value=2))
    await provider.store(MemoryEntry(key="c", value=3))  # evicts the oldest
    assert {e.key for e in await provider.list_entries()} == {"b", "c"}

    # An expired record frees capacity without evicting a live one.
    stale_key = "mem-test:memory:agent:b"
    payload = json.loads(backend.values[stale_key])
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    backend.values[stale_key] = json.dumps(payload)
    await provider.store(MemoryEntry(key="d", value=4))
    assert {e.key for e in await provider.list_entries()} == {"c", "d"}
    assert stale_key not in backend.index[index]


@pytest.mark.asyncio
async def test_overwriting_existing_key_does_not_evict_others():
    provider = _provider(policy=MemoryPolicy(max_entries=2))
    await provider.store(MemoryEntry(key="a", value=1))
    await provider.store(MemoryEntry(key="b", value=2))
    await provider.store(MemoryEntry(key="a", value=9))
    assert {e.key for e in await provider.list_entries()} == {"a", "b"}


@pytest.mark.asyncio
async def test_ttl_is_applied_as_redis_expiry_and_aclose_respects_ownership():
    backend = FakeRedisBackend()
    client = FakeRedis(backend)
    provider = RedisMemoryProvider(
        ENDPOINT, namespace="mem-test", client=client, policy=MemoryPolicy(ttl_seconds=30)
    )
    await provider.store(MemoryEntry(key="k", value="v"))
    assert next(iter(backend.expiry)).endswith(":k")

    await provider.aclose()  # injected client stays open
    await provider.aclose()
    assert client.closed is False


@pytest.mark.asyncio
async def test_owned_client_is_closed_once():
    closed: list[int] = []

    class OwnedClient(FakeRedis):
        async def aclose(self) -> None:
            closed.append(1)

    provider = _provider()
    provider._client = OwnedClient(FakeRedisBackend())
    provider._owns_client = True
    await provider.aclose()
    await provider.aclose()
    assert closed == [1]


@pytest.mark.asyncio
async def test_store_without_watch_support_falls_back_to_plain_transactions():
    class NoWatchPipeline:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in {"watch", "reset", "multi"}:
                raise AttributeError(name)
            return getattr(self._inner, name)

    class NoWatchClient(FakeRedis):
        def pipeline(self, *, transaction: bool = False):
            return NoWatchPipeline(super().pipeline(transaction=transaction))

    backend = FakeRedisBackend()
    provider = RedisMemoryProvider(
        ENDPOINT,
        namespace="mem-test",
        client=NoWatchClient(backend),
        policy=MemoryPolicy(max_entries=1, ttl_seconds=30),
    )
    await provider.store(MemoryEntry(key="a", value=1))
    await provider.store(MemoryEntry(key="b", value=2))
    assert {e.key for e in await provider.list_entries()} == {"b"}
