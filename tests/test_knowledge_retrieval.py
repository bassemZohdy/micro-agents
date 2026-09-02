"""Runtime-neutral knowledge retrieval semantics."""

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.knowledge import (
    InMemoryKnowledgeRetriever,
    KnowledgeSource,
    build_knowledge_query,
    retrieve_knowledge_context,
)
from micro_agent.models import ModelConfig, ModelProvider, ModelResponse
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


class RecordingProvider(ModelProvider):
    def __init__(self) -> None:
        self.messages: list[list[dict]] = []

    async def generate(
        self, config: ModelConfig, messages: list[dict], tools=None
    ) -> ModelResponse:
        self.messages.append(messages)
        return ModelResponse(content="ok")

    async def health_check(self) -> bool:
        return True


def _definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "knowledge-test", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Answer from trusted runtime context."},
                "dependencies": {
                    "model": {"ref": "test-model"},
                    "knowledge": [
                        {
                            "ref": "policy-kb",
                            "source_type": "document",
                            "version": "2026.09",
                            "max_results": 2,
                            "max_context_characters": 1200,
                        }
                    ],
                },
            },
        }
    )


def test_query_is_deterministic_and_uses_nested_values():
    assert build_knowledge_query({"z": ["refund", {"days": 30}], "a": "policy"}) == (
        "a policy z refund days 30"
    )


@pytest.mark.asyncio
async def test_context_is_bounded_deduplicated_and_framed_as_untrusted():
    retriever = InMemoryKnowledgeRetriever(
        {"kb": ["refund policy is thirty days", "refund policy is thirty days"]}
    )
    source = KnowledgeSource(ref="kb", max_results=5, max_context_characters=20)
    context, counts = await retrieve_knowledge_context(retriever, "refund policy", [source])
    assert "untrusted reference data" in context
    assert context.count("[source=kb") == 1
    assert counts == {"kb": 1}
    body = context.split("]\n", 1)[1]
    assert len(body) <= 20


@pytest.mark.asyncio
async def test_custom_runtime_retrieves_knowledge_before_model_call():
    provider = RecordingProvider()
    retriever = InMemoryKnowledgeRetriever(
        {
            "policy-kb": [
                {
                    "content": "Refund policy allows returns for thirty days.",
                    "version": "2026.09",
                },
                "Unrelated shipping details.",
            ]
        }
    )
    runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider, knowledge_provider=retriever))
    agent = await runtime.create(_definition())
    try:
        await runtime.start(agent)
        response = await runtime.invoke(
            agent, AgentRequest(input={"question": "What is the refund policy?"})
        )
        system = provider.messages[0][0]["content"]
        assert "Refund policy allows returns for thirty days." in system
        assert "Unrelated shipping details." not in system
        assert "untrusted reference data" in system
        assert response.metadata["knowledge_entries"] == 1
        assert response.metadata["knowledge_sources"] == {"policy-kb": 1}
    finally:
        await runtime.close()
