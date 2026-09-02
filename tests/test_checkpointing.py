"""Checkpoint persistence, capability, and resume acceptance tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from micro_agent.checkpoint import (
    CheckpointRecord,
    InMemoryCheckpointStore,
    SessionCheckpointStore,
)
from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import ModelConfig, ModelProvider, ModelResponse, ProviderCapabilities
from micro_agent.session import SqliteSessionProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


def _definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "checkpoint-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Answer briefly."},
                "dependencies": {"model": {"ref": "test-model"}},
            },
        }
    )


class _FailOnceProvider(ModelProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[list[dict]] = []

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        self.calls += 1
        self.messages.append([dict(message) for message in messages])
        if self.calls == 1:
            raise ConnectionError("transient model failure")
        return ModelResponse(content="resumed answer")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_checkpoint_capability_is_truthful() -> None:
    assert AdkRuntime().capabilities().checkpointing is False
    runtime = AdkRuntime(AdkRuntimeConfig(checkpoint_store=InMemoryCheckpointStore()))
    assert runtime.capabilities().checkpointing is True


@pytest.mark.asyncio
async def test_failed_invocation_can_resume_from_replay_safe_checkpoint() -> None:
    definition = _definition()
    provider = _FailOnceProvider()
    store = InMemoryCheckpointStore()

    runtime1 = AdkRuntime(AdkRuntimeConfig(model_provider=provider, checkpoint_store=store))
    agent1 = DefaultMicroAgent(definition, runtime1)
    await agent1.initialize()
    await agent1.start()
    with pytest.raises(ConnectionError, match="transient"):
        await agent1.invoke(AgentRequest(input={"question": "status"}, request_id="req-1"))
    checkpoint = await store.get("req-1")
    assert checkpoint is not None
    assert checkpoint.input_payload == {"question": "status"}
    assert checkpoint.iterations == 0
    await agent1.stop()
    await agent1.shutdown()

    runtime2 = AdkRuntime(AdkRuntimeConfig(model_provider=provider, checkpoint_store=store))
    agent2 = DefaultMicroAgent(definition, runtime2)
    await agent2.initialize()
    await agent2.start()
    response = await agent2.invoke(
        AgentRequest(input={}, request_id="resume-1", checkpoint_id="req-1")
    )
    assert response.output["content"] == "resumed answer"
    assert response.metadata["resumed_from_checkpoint"] == "req-1"
    assert provider.messages[0] == provider.messages[1]
    assert await store.get("req-1") is None
    await agent2.stop()
    await agent2.shutdown()


@pytest.mark.asyncio
async def test_session_checkpoint_store_persists_across_sqlite_provider_instances(
    tmp_path: Path,
) -> None:
    db = tmp_path / "checkpoint.db"
    provider1 = SqliteSessionProvider(str(db))
    store1 = SessionCheckpointStore(provider1)
    record = CheckpointRecord(
        checkpoint_id="cp-1",
        agent_id="agent-1",
        request_id="req-1",
        session_id="session-1",
        input_payload={"q": "hello"},
        messages=[{"role": "user", "content": "hello"}],
        iterations=2,
        usage={"completion_tokens": 3},
        history_tail_length=1,
    )
    await store1.save(record)
    await provider1.aclose()

    provider2 = SqliteSessionProvider(str(db))
    store2 = SessionCheckpointStore(provider2)
    restored = await store2.get("cp-1")
    assert restored is not None
    assert restored.input_payload == record.input_payload
    assert restored.messages == record.messages
    assert restored.iterations == 2
    await store2.delete("cp-1")
    assert await store2.get("cp-1") is None
    await provider2.aclose()
