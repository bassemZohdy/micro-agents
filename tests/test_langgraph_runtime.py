"""LangGraph adapter behavior beyond the shared runtime conformance suite."""

from __future__ import annotations

import sys

import pytest

from micro_agent.checkpoint import InMemoryCheckpointStore
from micro_agent.config.bootstrap import build_runtime
from micro_agent.core import AgentRequest, ContinuationNotFoundError
from micro_agent.definition import load_definition_from_dict
from micro_agent.memory import InMemoryMemoryProvider
from micro_agent.models import FakeModelConfig, FakeModelProvider, ModelResponse
from micro_agent.security import AgentPolicy, InMemoryApprovalStore
from micro_agent.session import InMemorySessionProvider

pytest.importorskip("langgraph.graph")
pytestmark = pytest.mark.langgraph

from runtimes.langgraph import (  # noqa: E402 - after the optional-dependency guard
    LangGraphError,
    LangGraphRuntime,
    LangGraphRuntimeConfig,
)


def _definition(*, tools: bool = True, name: str = "lg-agent", **runtime):
    spec: dict = {
        "behavior": {"instructions": "You are a LangGraph test agent."},
        "dependencies": {"model": {"ref": "lg-model", "provider": "fake"}},
    }
    if tools:
        spec["dependencies"]["tools"] = [{"name": "echo", "source": "native"}]
    if runtime:
        spec["runtime"] = runtime
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": name, "version": "1.0.0"},
            "spec": spec,
        }
    )


class _ToolCaller(FakeModelProvider):
    """Requests ``echo`` for the first ``tool_turns`` calls, then answers."""

    def __init__(self, tool_turns: int = 1) -> None:
        super().__init__(FakeModelConfig(response="final answer"))
        self.tool_turns = tool_turns
        self.calls = 0

    async def generate(self, config, messages, tools=None):
        await super().generate(config, messages, tools)
        self.calls += 1
        if self.calls <= self.tool_turns:
            return ModelResponse(tool_requests=[{"name": "echo", "arguments": {"message": "x"}}])
        return ModelResponse(content="final answer", finish_reason="stop")


async def _started(runtime, definition):
    agent = await runtime.create(definition)
    await runtime.start(agent)
    return agent


# ---------------------------------------------------------------------------
# Graph shape and capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_is_driven_by_a_real_langgraph_state_graph():
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=FakeModelProvider()))
    await runtime.create(_definition())
    graph = runtime._graph.get_graph()
    assert {"prepare", "model", "tools", "finalize"} <= set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("prepare", "model") in edges
    assert ("tools", "model") in edges  # the bounded agent loop
    assert ("model", "finalize") in edges
    await runtime.close()


@pytest.mark.asyncio
async def test_capabilities_are_honest():
    streaming = FakeModelProvider(FakeModelConfig(stream_chunks=["a", "b"]))
    runtime = LangGraphRuntime(
        LangGraphRuntimeConfig(model_provider=streaming, memory_provider=InMemoryMemoryProvider())
    )
    caps = runtime.capabilities()
    assert caps.streaming is True and caps.memory is True
    assert caps.checkpointing is False and caps.a2a is False
    await runtime.close()


def test_unsupported_checkpoint_store_is_rejected_instead_of_ignored():
    with pytest.raises(ValueError, match="checkpoint_store"):
        LangGraphRuntime(LangGraphRuntimeConfig(checkpoint_store=InMemoryCheckpointStore()))


def test_the_approval_store_is_supported():
    store = InMemoryApprovalStore()
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(approval_store=store))
    assert runtime._approval_store is store


@pytest.mark.asyncio
async def test_checkpoint_requests_are_rejected_and_unknown_continuations_fail_fast():
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=FakeModelProvider()))
    agent = await _started(runtime, _definition(tools=False))
    with pytest.raises(ValueError, match="checkpoint"):
        await runtime.invoke(agent, AgentRequest(input={}, checkpoint_id="cp-1"))
    with pytest.raises(ContinuationNotFoundError):
        await runtime.invoke(
            agent,
            AgentRequest(input={}, continuation_id="c-1", approval_decision="approve"),
        )
    await runtime.close()


def test_missing_extra_gives_an_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "langgraph.graph", None)
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=FakeModelProvider()))
    with pytest.raises(LangGraphError, match=r"micro-agents\[langgraph\]"):
        runtime._compile_graph()


# ---------------------------------------------------------------------------
# Loop semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tool_rounds_accumulate_results_and_usage():
    provider = _ToolCaller(tool_turns=2)
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=provider))
    agent = await _started(runtime, _definition())
    response = await runtime.invoke(agent, AgentRequest(input={"message": "go"}))
    assert response.output["content"] == "final answer"
    assert [r["tool"] for r in response.output["tool_results"]] == ["echo", "echo"]
    assert response.metadata["iterations"] == 3
    assert response.metadata["tools_called"] == ["echo", "echo"]
    assert response.metadata["max_iterations_reached"] is False
    assert response.metadata["usage"] == {}  # _ToolCaller reports no usage
    assert response.metadata["runtime"] == "langgraph"
    # Tool results are fed back to the model with their call ids.
    last_messages = provider.invocations[-1]["messages"]
    assert [m["role"] for m in last_messages if m["role"] == "tool"] == ["tool", "tool"]
    await runtime.close()


@pytest.mark.asyncio
async def test_max_iterations_bounds_the_loop():
    provider = _ToolCaller(tool_turns=99)
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=provider))
    agent = await _started(runtime, _definition(max_iterations=2))
    response = await runtime.invoke(agent, AgentRequest(input={}))
    assert provider.calls == 2
    assert response.metadata["iterations"] == 2
    assert response.metadata["max_iterations_reached"] is True
    await runtime.close()


@pytest.mark.asyncio
async def test_unknown_tool_requests_become_error_results_not_crashes():
    class UnknownTool(FakeModelProvider):
        calls = 0

        async def generate(self, config, messages, tools=None):
            UnknownTool.calls += 1
            if UnknownTool.calls == 1:
                return ModelResponse(tool_requests=[{"name": "ghost", "arguments": {}}])
            return ModelResponse(content="recovered")

    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=UnknownTool()))
    agent = await _started(runtime, _definition(tools=False))
    response = await runtime.invoke(agent, AgentRequest(input={}))
    assert response.output["content"] == "recovered"
    assert response.output["tool_results"][0]["error"] == "unknown tool: ghost"
    await runtime.close()


# ---------------------------------------------------------------------------
# Governance shared with the reference runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_denied_tool_is_not_executed():
    provider = _ToolCaller()
    runtime = LangGraphRuntime(
        LangGraphRuntimeConfig(model_provider=provider, policy=AgentPolicy(denied_tools=["echo"]))
    )
    agent = await _started(runtime, _definition())
    response = await runtime.invoke(agent, AgentRequest(input={}))
    result = response.output["tool_results"][0]
    assert result["denied"] is True and "denied by policy" in result["error"]
    assert response.metadata["tools_called"] == []
    await runtime.close()


def _approval_runtime(provider, store=None, **extra):
    return LangGraphRuntime(
        LangGraphRuntimeConfig(
            model_provider=provider,
            policy=AgentPolicy(approval_required=True),
            approval_store=store,
            **extra,
        )
    )


@pytest.mark.asyncio
async def test_tools_needing_approval_pause_with_a_durable_continuation():
    store = InMemoryApprovalStore()
    runtime = _approval_runtime(_ToolCaller(), store)
    agent = await _started(runtime, _definition())
    paused = await runtime.invoke(agent, AgentRequest(input={"q": 1}, session_id="s-1"))
    assert paused.status == "approval_required"
    assert paused.metadata["pending_tools"] == ["echo"]
    pending = await store.get(paused.metadata["continuation_id"])
    assert pending is not None
    assert pending.input_payload == {"q": 1} and pending.session_id == "s-1"
    assert pending.messages[-1]["role"] == "assistant" and "tool_calls" in pending.messages[-1]
    await runtime.close()


@pytest.mark.asyncio
async def test_approved_continuation_runs_the_wave_and_the_model_continues():
    provider = _ToolCaller()
    runtime = _approval_runtime(provider)
    agent = await _started(runtime, _definition())
    paused = await runtime.invoke(agent, AgentRequest(input={"q": 1}))
    resumed = await runtime.invoke(
        agent,
        AgentRequest(
            input={},
            continuation_id=paused.metadata["continuation_id"],
            approval_decision="approve",
        ),
    )
    assert resumed.status == "success" and resumed.output["content"] == "final answer"
    assert [r["tool"] for r in resumed.output["tool_results"]] == ["echo"]
    # The resumed model call sees the executed tool result with its call id.
    tool_messages = [m for m in provider.invocations[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1 and tool_messages[0]["tool_call_id"]
    await runtime.close()


@pytest.mark.asyncio
async def test_denied_continuation_tells_the_model_and_a_continuation_is_single_use():
    provider = _ToolCaller()
    runtime = _approval_runtime(provider)
    agent = await _started(runtime, _definition())
    paused = await runtime.invoke(agent, AgentRequest(input={}))
    decision = AgentRequest(
        input={},
        continuation_id=paused.metadata["continuation_id"],
        approval_decision="deny",
    )
    resumed = await runtime.invoke(agent, decision)
    assert resumed.metadata["tools_called"] == []
    assert resumed.output["tool_results"][0]["error"] == "denied by caller: approval denied"
    with pytest.raises(ContinuationNotFoundError):
        await runtime.invoke(agent, decision)
    await runtime.close()


@pytest.mark.asyncio
async def test_a_continuation_cannot_be_used_by_another_agent():
    store = InMemoryApprovalStore()
    runtime = _approval_runtime(_ToolCaller(), store)
    agent = await _started(runtime, _definition())
    paused = await runtime.invoke(agent, AgentRequest(input={}))
    other = await _started(runtime, _definition(name="someone-else"))
    with pytest.raises(ContinuationNotFoundError):
        await runtime.invoke(
            other,
            AgentRequest(
                input={},
                continuation_id=paused.metadata["continuation_id"],
                approval_decision="approve",
            ),
        )
    await runtime.close()


@pytest.mark.asyncio
async def test_hard_policy_denials_still_apply_after_approval():
    provider = _ToolCaller()
    runtime = LangGraphRuntime(
        LangGraphRuntimeConfig(
            model_provider=provider,
            policy=AgentPolicy(approval_required=True, denied_tools=["echo"]),
        )
    )
    agent = await _started(runtime, _definition())
    # A policy-denied tool never needs approval: it is simply denied.
    response = await runtime.invoke(agent, AgentRequest(input={}))
    assert response.status == "success"
    assert response.output["tool_results"][0]["denied"] is True
    await runtime.close()


@pytest.mark.asyncio
async def test_fallback_error_policy_still_applies():
    provider = FakeModelProvider(FakeModelConfig(should_error=True, error_message="down"))
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=provider))
    agent = await _started(runtime, _definition(tools=False, error_policy="fallback"))
    response = await runtime.invoke(agent, AgentRequest(input={}))
    assert response.status == "error" and "down" in response.error
    await runtime.close()


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_and_memory_are_written_after_the_graph_completes():
    sessions = InMemorySessionProvider()
    memory = InMemoryMemoryProvider()
    runtime = LangGraphRuntime(
        LangGraphRuntimeConfig(
            model_provider=_ToolCaller(),
            session_provider=sessions,
            memory_provider=memory,
        )
    )
    agent = await _started(runtime, _definition())
    await runtime.invoke(agent, AgentRequest(input={"message": "hi"}, session_id="lg-1"))
    session = await sessions.get("lg-1")
    assert session is not None
    # user, assistant tool-call turn, tool result, final assistant answer
    assert [m["role"] for m in session.messages] == ["user", "assistant", "tool", "assistant"]
    assert "tool_calls" in session.messages[1]
    await runtime.close()


@pytest.mark.asyncio
async def test_streaming_emits_deltas_then_the_normal_response():
    provider = FakeModelProvider(FakeModelConfig(response="ab", stream_chunks=["a", "b"]))
    runtime = LangGraphRuntime(LangGraphRuntimeConfig(model_provider=provider))
    agent = await _started(runtime, _definition(tools=False))
    events = [event async for event in runtime.stream(agent, AgentRequest(input={}))]
    assert [e.delta for e in events if e.delta] == ["a", "b"]
    assert events[-1].response is not None
    assert events[-1].response.output["content"] == "ab"
    await runtime.close()


# ---------------------------------------------------------------------------
# Bootstrap selection
# ---------------------------------------------------------------------------


def test_bootstrap_selects_the_langgraph_runtime(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "langgraph")
    bootstrap = build_runtime(_definition(tools=False))
    assert isinstance(bootstrap.runtime, LangGraphRuntime)
    assert bootstrap.runtime.capabilities().checkpointing is False


def test_bootstrap_hands_the_approval_store_to_langgraph(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "langgraph")
    store = InMemoryApprovalStore()
    bootstrap = build_runtime(_definition(tools=False), approval_store=store)
    assert isinstance(bootstrap.runtime, LangGraphRuntime)
    assert bootstrap.runtime._approval_store is store
