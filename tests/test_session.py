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
from tests.fake_redis import FakeRedis, FakeRedisBackend


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
        backend = FakeRedisBackend()
        replica_a = RedisSessionProvider(client=FakeRedis(backend), ttl_seconds=60)
        replica_b = RedisSessionProvider(client=FakeRedis(backend), ttl_seconds=60)

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
        backend = FakeRedisBackend()
        provider = RedisSessionProvider(client=FakeRedis(backend))
        await asyncio.gather(*(provider.create(f"session-{i}") for i in range(20)))
        active = await provider.list_active()
        assert {meta.session_id for meta in active} == {f"session-{i}" for i in range(20)}
        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_close_does_not_close_injected_client(self):
        client = FakeRedis(FakeRedisBackend())
        provider = RedisSessionProvider(client=client)
        await provider.aclose()
        await provider.aclose()
        assert client.closed is False
