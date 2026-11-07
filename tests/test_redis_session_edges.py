"""Edge cases for the Redis session provider using the shared fake client."""

import json

import pytest

from micro_agent.session import RedisSessionProvider
from micro_agent.session.session import SessionContext
from micro_agent.state import StateConflictError
from tests.fake_redis import FakeRedis, FakeRedisBackend

ENDPOINT = "redis://redis.example.test/0"
INDEX = "sess-test:sessions"
PREFIX = "sess-test:session:"


def _provider(backend: FakeRedisBackend | None = None, **kwargs) -> RedisSessionProvider:
    backend = backend or FakeRedisBackend()
    return RedisSessionProvider(
        ENDPOINT, namespace="sess-test", client=FakeRedis(backend), **kwargs
    )


@pytest.mark.parametrize("ttl", [-1, True, 1.5, "60"])
def test_ttl_must_be_a_non_negative_integer(ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        _provider(ttl_seconds=ttl)


@pytest.mark.parametrize("namespace", ["", " x", "x "])
def test_namespace_must_be_trimmed_and_non_empty(namespace):
    with pytest.raises(ValueError, match="namespace"):
        RedisSessionProvider(ENDPOINT, namespace=namespace, client=FakeRedis(FakeRedisBackend()))


@pytest.mark.parametrize("endpoint", ["http://nope", "redis://", "localhost:6379"])
def test_endpoint_scheme_and_host_are_validated(endpoint):
    with pytest.raises(ValueError, match="redis://"):
        RedisSessionProvider(endpoint, client=FakeRedis(FakeRedisBackend()))


@pytest.mark.asyncio
async def test_create_get_update_delete_roundtrip_with_tenant_isolation():
    provider = _provider()
    created = await provider.create("s1", tenant_id="t/1")
    assert created.version == 1 and "created_at" in created.metadata
    await provider.create("s1", tenant_id="t2")

    fetched = await provider.get("s1", tenant_id="t/1")
    assert fetched is not None
    fetched.messages.append({"role": "user", "content": "hi"})
    fetched.caller_context["who"] = "me"
    await provider.update(fetched)

    again = await provider.get("s1", tenant_id="t/1")
    assert again is not None and again.version == 2
    assert again.messages == [{"role": "user", "content": "hi"}]
    assert again.caller_context == {"who": "me"}
    assert (await provider.get("s1", tenant_id="t2")).messages == []  # type: ignore[union-attr]
    assert await provider.get("s1") is None

    assert [m.session_id for m in await provider.list_active(tenant_id="t2")] == ["s1"]
    assert len(await provider.list_active()) == 2

    await provider.delete("s1", tenant_id="t/1")
    assert await provider.get("s1", tenant_id="t/1") is None
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_generated_session_id_and_optimistic_conflicts():
    provider = _provider()
    created = await provider.create()
    assert created.session_id

    stale = SessionContext(session_id=created.session_id, version=1)
    await provider.update(SessionContext(session_id=created.session_id, version=1))
    with pytest.raises(StateConflictError):
        await provider.update(stale)  # version 1 is now stale (actual: 2)
    with pytest.raises(StateConflictError):
        await provider.update(SessionContext(session_id="never-created", version=3))
    # An explicit expected_version overrides the session's own version.
    await provider.update(SessionContext(session_id=created.session_id), expected_version=2)


@pytest.mark.asyncio
async def test_update_creates_missing_session_and_preserves_created_at():
    provider = _provider()
    await provider.update(SessionContext(session_id="fresh"))
    fresh = await provider.get("fresh")
    assert fresh is not None and fresh.version == 1

    original_created = fresh.metadata["created_at"]
    replacement = SessionContext(session_id="fresh", version=1)
    await provider.update(replacement)
    assert (await provider.get("fresh")).metadata["created_at"] == original_created  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_default_ttl_is_applied_and_update_ttl_extends_expiry():
    backend = FakeRedisBackend()
    provider = _provider(backend, ttl_seconds=60)
    session = await provider.create("s1")
    assert session.metadata["expires_at"]
    assert f"{PREFIX}s1" in backend.expiry

    session.messages.append({"role": "user", "content": "x"})
    await provider.update(session, ttl_seconds=3600)
    assert (await provider.get("s1")).metadata["expires_at"] > session.metadata["created_at"]  # type: ignore[union-attr]

    # Updating without a ttl keeps the existing expiry instead of clearing it.
    latest = await provider.get("s1")
    assert latest is not None
    kept = latest.metadata["expires_at"]
    await provider.update(SessionContext(session_id="s1", version=latest.version))
    assert (await provider.get("s1")).metadata["expires_at"] == kept  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_expired_sessions_are_removed_on_get_and_hidden_from_list():
    backend = FakeRedisBackend()
    provider = _provider(backend, ttl_seconds=60)
    await provider.create("old")
    await provider.create("new")
    payload = json.loads(backend.values[f"{PREFIX}old"])
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    backend.values[f"{PREFIX}old"] = json.dumps(payload)

    assert [m.session_id for m in await provider.list_active()] == ["new"]
    assert f"{PREFIX}old" not in backend.values
    assert f"{PREFIX}old" not in backend.index.get(INDEX, {})

    await provider.create("old2")
    backend.values[f"{PREFIX}old2"] = json.dumps(
        {**json.loads(backend.values[f"{PREFIX}old2"]), "expires_at": "2000-01-01T00:00:00+00:00"}
    )
    assert await provider.get("old2") is None
    assert f"{PREFIX}old2" not in backend.values


@pytest.mark.asyncio
async def test_unparseable_expiry_is_treated_as_live_not_expired():
    backend = FakeRedisBackend()
    provider = _provider(backend, ttl_seconds=60)
    await provider.create("s1")
    backend.values[f"{PREFIX}s1"] = json.dumps(
        {**json.loads(backend.values[f"{PREFIX}s1"]), "expires_at": "not-a-date"}
    )
    assert await provider.get("s1") is not None
    assert [m.session_id for m in await provider.list_active()] == ["s1"]


@pytest.mark.asyncio
async def test_corrupt_and_orphaned_records_are_scrubbed():
    backend = FakeRedisBackend()
    provider = _provider(backend)
    await provider.create("good")
    for name, raw in {"bad-json": "{nope", "not-object": "[1]", "no-id": '{"x": 1}'}.items():
        backend.values[f"{PREFIX}{name}"] = raw
        backend.index[INDEX][f"{PREFIX}{name}"] = 0.0
    backend.index[INDEX][f"{PREFIX}orphan"] = 0.0

    assert [m.session_id for m in await provider.list_active()] == ["good"]
    assert set(backend.index[INDEX]) == {f"{PREFIX}good"}

    backend.values[f"{PREFIX}rotten"] = "{nope"
    backend.index[INDEX][f"{PREFIX}rotten"] = 0.0
    assert await provider.get("rotten") is None
    assert f"{PREFIX}rotten" not in backend.values
    backend.index[INDEX][f"{PREFIX}ghost"] = 0.0
    assert await provider.get("ghost") is None
    assert f"{PREFIX}ghost" not in backend.index[INDEX]


@pytest.mark.asyncio
async def test_update_overwrites_a_corrupt_existing_record():
    backend = FakeRedisBackend()
    provider = _provider(backend)
    backend.values[f"{PREFIX}s1"] = "{nope"
    await provider.update(SessionContext(session_id="s1"))
    assert (await provider.get("s1")).version == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_update_with_a_past_expiry_deletes_the_session():
    backend = FakeRedisBackend()
    provider = _provider(backend, ttl_seconds=60)
    await provider.create("s1")
    await provider.update(SessionContext(session_id="s1", version=1), ttl_seconds=0)
    assert await provider.get("s1") is None
    assert f"{PREFIX}s1" not in backend.values


@pytest.mark.asyncio
async def test_update_without_watch_support_uses_a_plain_transaction():
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

    provider = RedisSessionProvider(
        ENDPOINT,
        namespace="sess-test",
        client=NoWatchClient(FakeRedisBackend()),
        ttl_seconds=60,
    )
    await provider.update(SessionContext(session_id="s1"))
    await provider.update(SessionContext(session_id="s1", version=1))
    assert (await provider.get("s1")).version == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_persistent_watch_conflicts_exhaust_retries():
    class WatchError(Exception):
        pass

    class ConflictPipeline:
        async def watch(self, *keys):
            return None

        async def get(self, key):
            return None

        def multi(self):
            return self

        def set(self, *args, **kwargs):
            return self

        def zadd(self, *args, **kwargs):
            return self

        async def execute(self):
            raise WatchError("always conflicts")

        async def reset(self):
            return None

    class ConflictClient(FakeRedis):
        def pipeline(self, *, transaction: bool = False):
            return ConflictPipeline()

    provider = RedisSessionProvider(
        ENDPOINT, namespace="sess-test", client=ConflictClient(FakeRedisBackend())
    )
    with pytest.raises(StateConflictError):
        await provider.update(SessionContext(session_id="s1"))


@pytest.mark.asyncio
async def test_non_conflict_errors_are_not_swallowed():
    class BrokenPipeline:
        async def watch(self, *keys):
            raise ConnectionError("redis down")

        async def reset(self):
            return None

    class BrokenClient(FakeRedis):
        def pipeline(self, *, transaction: bool = False):
            return BrokenPipeline()

    provider = RedisSessionProvider(
        ENDPOINT, namespace="sess-test", client=BrokenClient(FakeRedisBackend())
    )
    with pytest.raises(ConnectionError):
        await provider.update(SessionContext(session_id="s1"))


@pytest.mark.asyncio
async def test_aclose_closes_owned_clients_once_and_never_injected_ones():
    injected = _provider()
    await injected.aclose()
    assert injected._client.closed is False

    closed: list[int] = []

    class Owned(FakeRedis):
        async def aclose(self) -> None:
            closed.append(1)

    owned = _provider()
    owned._client = Owned(FakeRedisBackend())
    owned._owns_client = True
    await owned.aclose()
    await owned.aclose()
    assert closed == [1]
