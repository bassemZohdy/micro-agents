"""Tests for Micro-Agent Knowledge."""

import httpx
import pytest

from micro_agent.knowledge import (
    HttpKnowledgeRetriever,
    KnowledgeEntry,
    KnowledgeProviderError,
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


@pytest.mark.asyncio
async def test_http_knowledge_retriever_bounds_and_propagates_search_context() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/health/ready"):
            return httpx.Response(200)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "content": "Refunds are allowed for thirty days.",
                        "score": 0.91,
                        "version": "2026.09",
                        "metadata": {"document_id": "refunds"},
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://search.test/api/"
    )
    retriever = HttpKnowledgeRetriever(
        "https://search.test/api", bearer_token="search-token", client=client
    )
    source = KnowledgeSource(
        ref="policy-kb", version="2026.09", max_results=1, metadata={"tenant_id": "acme"}
    )
    try:
        entries = await retriever.retrieve("refund policy", source, limit=5)
        assert entries[0].content.startswith("Refunds")
        assert entries[0].relevance == 0.91
        assert entries[0].metadata["content_hash"]
        assert await retriever.health_check(source)
        search = requests[0]
        assert search.url.path == "/api/search"
        assert search.headers["Authorization"] == "Bearer search-token"
        assert search.content == (
            b'{"query":"refund policy","source_ref":"policy-kb","limit":1,'
            b'"version":"2026.09","tenant_id":"acme"}'
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_knowledge_retriever_rejects_invalid_results() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": [{}]})),
        base_url="https://search.test",
    )
    retriever = HttpKnowledgeRetriever("https://search.test", client=client)
    try:
        with pytest.raises(KnowledgeProviderError, match="content"):
            await retriever.retrieve("query", KnowledgeSource(ref="kb"))
    finally:
        await client.aclose()


def test_http_knowledge_endpoint_requires_https_outside_loopback() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpKnowledgeRetriever("http://search.example")
