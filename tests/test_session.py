"""Tests for Micro-Agent Session."""

import asyncio

import pytest

from micro_agent.session import (
    InMemorySessionProvider,
    RedisSessionProvider,
    SessionContext,
    SessionMetadata,
    SessionProvider,
)


class _FakeRedisBackend:
    """Small async Redis test double shared by multiple provider instances."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        self.index: dict[str, float] = {}


class _FakeRedisPipeline:
    def __init__(self, client: "_FakeRedis") -> None:
        self._client = client
        self._commands: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def set(self, *args: object, **kwargs: object) -> "_FakeRedisPipeline":
        self._commands.append(("set", args, kwargs))
        return self

    def delete(self, *args: object, **kwargs: object) -> "_FakeRedisPipeline":
        self._commands.append(("delete", args, kwargs))
        return self

    def zadd(self, *args: object, **kwargs: object) -> "_FakeRedisPipeline":
        self._commands.append(("zadd", args, kwargs))
        return self

    def zrem(self, *args: object, **kwargs: object) -> "_FakeRedisPipeline":
        self._commands.append(("zrem", args, kwargs))
        return self

    async def execute(self) -> list[object]:
        results: list[object] = []
        for name, args, kwargs in self._commands:
            result = getattr(self._client, name)(*args, **kwargs)
            results.append(await result)
        return results


class _FakeRedis:
    def __init__(self, backend: _FakeRedisBackend) -> None:
        self.backend = backend
        self.closed = False

    def _purge(self, key: str) -> None:
        if self.backend.expiry.get(key, float("inf")) <= asyncio.get_running_loop().time():
            self.backend.values.pop(key, None)
            self.backend.expiry.pop(key, None)

    def pipeline(self, *, transaction: bool = False) -> _FakeRedisPipeline:
        assert transaction is True
        return _FakeRedisPipeline(self)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        self.backend.values[key] = value
        if ex is not None:
            self.backend.expiry[key] = asyncio.get_running_loop().time() + ex
        else:
            self.backend.expiry.pop(key, None)
        return True

    async def get(self, key: str) -> str | None:
        self._purge(key)
        return self.backend.values.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [await self.get(key) for key in keys]

    async def delete(self, key: str) -> int:
        existed = key in self.backend.values
        self.backend.values.pop(key, None)
        self.backend.expiry.pop(key, None)
        return int(existed)

    async def zadd(self, index: str, values: dict[str, float]) -> int:
        del index
        added = 0
        for key, score in values.items():
            if key not in self.backend.index:
                added += 1
            self.backend.index[key] = score
        return added

    async def zrem(self, index: str, key: str) -> int:
        del index
        return int(self.backend.index.pop(key, None) is not None)

    async def zrange(self, index: str, start: int, stop: int) -> list[str]:
        del index
        ordered = [
            key for key, _score in sorted(self.backend.index.items(), key=lambda item: item[1])
        ]
        if stop == -1:
            return ordered[start:]
        return ordered[start : stop + 1]

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


class TestSessionContext:
    """Test session context."""

    def test_default_context(self):
        ctx = SessionContext()
        assert ctx.session_id
        assert ctx.messages == []
        assert ctx.metadata == {}

    def test_context_with_id(self):
        ctx = SessionContext(session_id="sess-123")
        assert ctx.session_id == "sess-123"


class TestSessionMetadata:
    """Test session metadata."""

    def test_metadata_creation(self):
        meta = SessionMetadata(session_id="sess-1")
        assert meta.session_id == "sess-1"
        assert meta.is_active is True


class TestSessionProviderInterface:
    """Test that SessionProvider is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SessionProvider()  # type: ignore[abstract]


class TestInMemorySessionProvider:
    """Test in-memory session provider."""

    @pytest.mark.asyncio
    async def test_create_session(self):
        provider = InMemorySessionProvider()
        session = await provider.create("sess-1")
        assert session.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_create_auto_id(self):
        provider = InMemorySessionProvider()
        session = await provider.create()
        assert session.session_id

    @pytest.mark.asyncio
    async def test_get_session(self):
        provider = InMemorySessionProvider()
        await provider.create("sess-1")
        session = await provider.get("sess-1")
        assert session is not None
        assert session.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        provider = InMemorySessionProvider()
        session = await provider.get("missing")
        assert session is None

    @pytest.mark.asyncio
    async def test_update_session(self):
        provider = InMemorySessionProvider()
        session = await provider.create("sess-1")
        session.messages.append({"role": "user", "content": "hello"})
        await provider.update(session)
        retrieved = await provider.get("sess-1")
        assert len(retrieved.messages) == 1

    @pytest.mark.asyncio
    async def test_delete_session(self):
        provider = InMemorySessionProvider()
        await provider.create("sess-1")
        await provider.delete("sess-1")
        assert await provider.get("sess-1") is None

    @pytest.mark.asyncio
    async def test_list_active(self):
        provider = InMemorySessionProvider()
        await provider.create("sess-1")
        await provider.create("sess-2")
        active = await provider.list_active()
        assert len(active) == 2


class TestSqliteSessionProvider:
    """SQLite operations are serialized for one development provider."""

    @pytest.mark.asyncio
    async def test_concurrent_operations_are_serialized(self, tmp_path):
        from micro_agent.session import SqliteSessionProvider

        provider = SqliteSessionProvider(str(tmp_path / "sessions.db"))
        try:
            await asyncio.gather(*(provider.create(f"session-{index}") for index in range(20)))
            active = await provider.list_active()
            assert {meta.session_id for meta in active} == {
                f"session-{index}" for index in range(20)
            }
        finally:
            await provider.aclose()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        from micro_agent.session import SqliteSessionProvider

        provider = SqliteSessionProvider()
        await provider.aclose()
        await provider.aclose()


class TestRedisSessionProvider:
    """Redis sessions share state through atomic pipelines."""

    @pytest.mark.asyncio
    async def test_replicas_share_sessions_and_expiration_metadata(self):
        backend = _FakeRedisBackend()
        replica_a = RedisSessionProvider(client=_FakeRedis(backend), ttl_seconds=60)
        replica_b = RedisSessionProvider(client=_FakeRedis(backend), ttl_seconds=60)

        session = await replica_a.create("shared-session")
        session.messages.append({"role": "user", "content": "from replica A"})
        await replica_a.update(session)

        seen = await replica_b.get("shared-session")
        assert seen is not None
        assert seen.messages[-1]["content"] == "from replica A"
        assert seen.metadata["created_at"] == session.metadata["created_at"]
        assert (await replica_b.list_active())[0].session_id == "shared-session"

        await replica_a.delete("shared-session")
        assert await replica_b.get("shared-session") is None
        await replica_a.aclose()
        await replica_b.aclose()

    @pytest.mark.asyncio
    async def test_concurrent_creates_are_visible_to_all_replicas(self):
        backend = _FakeRedisBackend()
        provider = RedisSessionProvider(client=_FakeRedis(backend))
        await asyncio.gather(*(provider.create(f"session-{i}") for i in range(20)))
        active = await provider.list_active()
        assert {meta.session_id for meta in active} == {f"session-{i}" for i in range(20)}
        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_close_does_not_close_injected_client(self):
        client = _FakeRedis(_FakeRedisBackend())
        provider = RedisSessionProvider(client=client)
        await provider.aclose()
        await provider.aclose()
        assert client.closed is False
