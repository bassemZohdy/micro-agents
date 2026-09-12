"""Tests for Micro-Agent Knowledge."""

import pytest

from micro_agent.knowledge import (
    KnowledgeEntry,
    KnowledgeRetriever,
    KnowledgeSource,
    SqliteKnowledgeRetriever,
)


class TestKnowledgeSource:
    """Test knowledge source."""

    def test_basic_source(self):
        source = KnowledgeSource(ref="residency-rules")
        assert source.ref == "residency-rules"
        assert source.source_type is None

    def test_full_source(self):
        source = KnowledgeSource(
            ref="residency-rules",
            source_type="document",
            version="2024.1",
        )
        assert source.source_type == "document"
        assert source.version == "2024.1"


class TestKnowledgeEntry:
    """Test knowledge entry."""

    def test_basic_entry(self):
        entry = KnowledgeEntry(content="Residency rules content")
        assert entry.content == "Residency rules content"
        assert entry.relevance == 1.0

    def test_entry_with_source(self):
        entry = KnowledgeEntry(
            content="Rule A",
            source_ref="residency-rules",
            relevance=0.95,
        )
        assert entry.source_ref == "residency-rules"
        assert entry.relevance == 0.95


class TestKnowledgeRetrieverInterface:
    """Test that KnowledgeRetriever is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            KnowledgeRetriever()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_sqlite_knowledge_is_versioned_and_tenant_scoped(tmp_path) -> None:
    retriever = SqliteKnowledgeRetriever(tmp_path / "knowledge.db")
    tenant_a = KnowledgeSource(ref="rules", version="v2", metadata={"tenant_id": "a"})
    tenant_b = KnowledgeSource(ref="rules", metadata={"tenant_id": "b"})
    document_id = await retriever.add_document(
        tenant_a, "Tenant A renewal rule", document_id="rule-1"
    )
    await retriever.add_document(tenant_b, "Tenant B renewal rule", document_id="rule-1")

    entries = await retriever.retrieve("renewal", tenant_a)
    assert document_id == "rule-1"
    assert entries[0].content == "Tenant A renewal rule"
    assert entries[0].metadata["version"] == "v2"
    assert await retriever.retrieve("renewal", tenant_b)
    assert await retriever.health_check(tenant_a)
    await retriever.delete_document(tenant_a, "rule-1")
    assert await retriever.retrieve("renewal", tenant_a) == []
    await retriever.close()
