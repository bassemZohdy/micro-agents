"""Tests for Micro-Agent Session."""

import pytest

from micro_agent.session import (
    InMemorySessionProvider,
    SessionContext,
    SessionMetadata,
    SessionProvider,
)


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
