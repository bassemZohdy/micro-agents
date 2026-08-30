"""Behavioral tests for session lifecycle, memory policy, knowledge retriever."""

import pytest

from micro_agent.knowledge import (
    InMemoryKnowledgeRetriever,
    KnowledgeSource,
    compute_content_hash,
)
from micro_agent.memory import InMemoryMemoryProvider, MemoryEntry, MemoryPolicy
from micro_agent.session import InMemorySessionProvider


class TestSessionLifecycle:
    """Session lifecycle: created_at, expires_at, expiration enforcement."""

    @pytest.mark.asyncio
    async def test_create_sets_created_at(self):
        provider = InMemorySessionProvider()
        session = await provider.create("sess-1")
        assert session.metadata["created_at"]
        active = await provider.list_active()
        assert active[0].created_at == session.metadata["created_at"]

    @pytest.mark.asyncio
    async def test_ttl_sets_expires_at(self):
        provider = InMemorySessionProvider()
        session = await provider.create("sess-1", ttl_seconds=3600)
        meta = (await provider.list_active())[0]
        assert meta.expires_at is not None
        assert meta.expires_at > session.metadata["created_at"]

    @pytest.mark.asyncio
    async def test_expired_session_returns_none(self):
        provider = InMemorySessionProvider()
        await provider.create("sess-1", ttl_seconds=0)
        assert await provider.get("sess-1") is None
        assert await provider.list_active() == []

    @pytest.mark.asyncio
    async def test_update_refreshes_expiration(self):
        provider = InMemorySessionProvider()
        await provider.create("sess-1", ttl_seconds=0)
        session = await provider.get("sess-1")
        assert session is None  # already expired

        await provider.create("sess-2")
        session = await provider.get("sess-2")
        assert session is not None
        await provider.update(session, ttl_seconds=0)
        assert await provider.get("sess-2") is None

    @pytest.mark.asyncio
    async def test_no_ttl_never_expires(self):
        provider = InMemorySessionProvider()
        await provider.create("sess-1")
        meta = (await provider.list_active())[0]
        assert meta.expires_at is None
        assert await provider.get("sess-1") is not None


class TestMemoryPolicyEnforcement:
    """MemoryPolicy enforcement: max_entries, ttl_seconds, auto_store."""

    @pytest.mark.asyncio
    async def test_max_entries_evicts_oldest(self):
        provider = InMemoryMemoryProvider(policy=MemoryPolicy(max_entries=2))
        await provider.store(MemoryEntry(key="a", value="1"))
        await provider.store(MemoryEntry(key="b", value="2"))
        await provider.store(MemoryEntry(key="c", value="3"))
        assert await provider.get("a") is None
        assert await provider.get("b") is not None
        assert await provider.get("c") is not None

    @pytest.mark.asyncio
    async def test_restore_does_not_evict_itself(self):
        provider = InMemoryMemoryProvider(policy=MemoryPolicy(max_entries=2))
        await provider.store(MemoryEntry(key="a", value="1"))
        await provider.store(MemoryEntry(key="b", value="2"))
        await provider.store(MemoryEntry(key="a", value="1b"))
        restored = await provider.get("a")
        assert restored is not None and restored.value == "1b"
        assert await provider.get("b") is not None

    @pytest.mark.asyncio
    async def test_ttl_expires_entries(self):
        provider = InMemoryMemoryProvider(policy=MemoryPolicy(ttl_seconds=0))
        await provider.store(MemoryEntry(key="a", value="1"))
        assert await provider.get("a") is None
        assert await provider.list_entries() == []
        assert await provider.search("1") == []

    @pytest.mark.asyncio
    async def test_no_policy_keeps_entries(self):
        provider = InMemoryMemoryProvider()
        await provider.store(MemoryEntry(key="a", value="1"))
        assert await provider.get("a") is not None

    @pytest.mark.asyncio
    async def test_auto_store_flag_exposed(self):
        provider = InMemoryMemoryProvider(policy=MemoryPolicy(auto_store=True))
        assert provider.policy.auto_store is True


class TestInMemoryKnowledgeRetriever:
    """Concrete knowledge retriever with integrity metadata."""

    @pytest.mark.asyncio
    async def test_retrieve_matches_by_relevance(self):
        retriever = InMemoryKnowledgeRetriever(
            {
                "rules": [
                    "Eligibility requires one year of residency.",
                    "Parking permits are issued at the town hall.",
                ]
            }
        )
        entries = await retriever.retrieve("eligibility residency", KnowledgeSource(ref="rules"))
        assert entries
        assert entries[0].source_ref == "rules"
        assert "Eligibility" in entries[0].content

    @pytest.mark.asyncio
    async def test_entries_carry_content_hash_and_version(self):
        retriever = InMemoryKnowledgeRetriever()
        content = "Residency rule: proof of address required."
        retriever.add_document(KnowledgeSource(ref="rules", version="2024.1"), content)
        source = KnowledgeSource(ref="rules", version="2024.1")
        entries = await retriever.retrieve("proof of address", source)
        assert entries[0].metadata["content_hash"] == compute_content_hash(content)
        assert entries[0].metadata["version"] == "2024.1"

    @pytest.mark.asyncio
    async def test_health_check(self):
        retriever = InMemoryKnowledgeRetriever({"rules": ["doc"]})
        assert await retriever.health_check(KnowledgeSource(ref="rules")) is True
        assert await retriever.health_check(KnowledgeSource(ref="missing")) is False

    @pytest.mark.asyncio
    async def test_unknown_source_returns_empty(self):
        retriever = InMemoryKnowledgeRetriever()
        entries = await retriever.retrieve("anything", KnowledgeSource(ref="missing"))
        assert entries == []
