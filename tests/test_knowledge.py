"""Tests for Micro-Agent Knowledge."""

import pytest

from micro_agent.knowledge import (
    KnowledgeEntry,
    KnowledgeRetriever,
    KnowledgeSource,
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
