"""Runtime conformance suite: one contract, every registered runtime.

The ``AgentRuntime`` SPI promises behavior, not just method names. These tests
run the same scenarios against the reference loop, the Google ADK adapter and
the LangGraph adapter, so a new runtime proves itself by being added to
``RUNTIMES`` rather than by writing bespoke tests.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelConfig, FakeModelProvider
from micro_agent.runtime import AgentRuntime
from micro_agent.session import InMemorySessionProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


def _custom(provider, **services) -> AgentRuntime:
    return AdkRuntime(AdkRuntimeConfig(model_provider=provider, **services))


def _google_adk(provider, **services) -> AgentRuntime:
    from runtimes.google_adk import GoogleAdkRuntime, GoogleAdkRuntimeConfig

    return GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider, **services))


def _langgraph(provider, **services) -> AgentRuntime:
    from runtimes.langgraph import LangGraphRuntime, LangGraphRuntimeConfig

    return LangGraphRuntime(LangGraphRuntimeConfig(model_provider=provider, **services))


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ImportError:  # parent package missing
        return False


def _runtime_params():
    params = [pytest.param(_custom, id="custom")]
    for name, module, factory, marker in (
        ("google-adk", "google.adk", _google_adk, pytest.mark.adk),
        ("langgraph", "langgraph.graph", _langgraph, pytest.mark.langgraph),
    ):
        available = _importable(module)
        params.append(
            pytest.param(
                factory,
                id=name,
                marks=[
                    marker,
                    pytest.mark.skipif(not available, reason=f"{module} is not installed"),
                ],
            )
        )
    return params


@pytest.fixture(params=_runtime_params())
def make_runtime(request):
    return request.param


def _definition(*, tools: bool = False, max_iterations: int | None = None):
    spec: dict = {
        "behavior": {"instructions": "Answer briefly."},
        "dependencies": {"model": {"ref": "conformance-model", "provider": "fake"}},
    }
    if tools:
        spec["dependencies"]["tools"] = [{"name": "echo", "source": "native"}]
    if max_iterations is not None:
        spec["runtime"] = {"max_iterations": max_iterations}
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "conformance-agent", "version": "1.0.0"},
            "spec": spec,
        }
    )


async def _run(runtime: AgentRuntime, definition, request: AgentRequest):
    agent = await runtime.create(definition)
    try:
        await runtime.start(agent)
        return await runtime.invoke(agent, request)
    finally:
        await runtime.shutdown(agent)
        await runtime.close()


@pytest.mark.asyncio
async def test_reports_a_capability_matrix(make_runtime):
    runtime = make_runtime(FakeModelProvider())
    capabilities = runtime.capabilities().as_dict()
    assert set(capabilities) == {
        "streaming",
        "memory",
        "mcp",
        "a2a",
        "structured_output",
        "checkpointing",
    }
    assert all(isinstance(value, bool) for value in capabilities.values())
    sources = runtime.capabilities().sources()
    assert set(sources) == set(capabilities)
    for name, supported in capabilities.items():
        assert sources[name] in ({"native", "provided"} if supported else {"none"})
    await runtime.close()


@pytest.mark.asyncio
async def test_create_exposes_identity_without_framework_types(make_runtime):
    runtime = make_runtime(FakeModelProvider())
    agent = await runtime.create(_definition())
    assert agent.identity.agent_name == "conformance-agent"
    assert agent.identity.agent_version == "1.0.0"
    assert type(agent.identity).__module__.startswith("micro_agent.")
    await runtime.shutdown(agent)
    await runtime.close()


@pytest.mark.asyncio
async def test_simple_invocation_returns_the_model_content(make_runtime):
    provider = FakeModelProvider(FakeModelConfig(response="conformant"))
    response = await _run(
        make_runtime(provider),
        _definition(),
        AgentRequest(input={"message": "hi"}, session_id="s-1"),
    )
    assert response.status == "success"
    assert response.output["content"] == "conformant"
    assert response.session_id == "s-1"


@pytest.mark.asyncio
async def test_session_history_is_replayed_to_the_model(make_runtime):
    """The storage layout is runtime-specific; replayed history is the contract."""
    provider = FakeModelProvider(FakeModelConfig(response="remembered"))
    runtime = make_runtime(provider, session_provider=InMemorySessionProvider())
    agent = await runtime.create(_definition())
    try:
        await runtime.start(agent)
        await runtime.invoke(agent, AgentRequest(input={"message": "first-turn"}, session_id="s-2"))
        await runtime.invoke(
            agent, AgentRequest(input={"message": "second-turn"}, session_id="s-2")
        )
    finally:
        await runtime.close()
    second_call = json.dumps(provider.invocations[-1]["messages"], default=str)
    assert "first-turn" in second_call
    assert "second-turn" in second_call
    assert "remembered" in second_call


@pytest.mark.asyncio
async def test_tool_loop_executes_requested_tools_then_answers(make_runtime):
    calls = {"n": 0}

    class ToolThenAnswer(FakeModelProvider):
        async def generate(self, config, messages, tools=None):
            from micro_agent.models import ModelResponse

            calls["n"] += 1
            if calls["n"] == 1:
                return ModelResponse(
                    content="",
                    tool_requests=[{"name": "echo", "arguments": {"message": "ping"}}],
                )
            return ModelResponse(content="done after tool")

    response = await _run(
        make_runtime(ToolThenAnswer()),
        _definition(tools=True),
        AgentRequest(input={"message": "go"}),
    )
    assert response.status == "success"
    assert response.output["content"] == "done after tool"
    assert [result["tool"] for result in response.output["tool_results"]] == ["echo"]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_model_failure_surfaces_as_an_error(make_runtime):
    provider = FakeModelProvider(FakeModelConfig(should_error=True, error_message="boom"))
    with pytest.raises(Exception, match="boom"):
        await _run(make_runtime(provider), _definition(), AgentRequest(input={}))


@pytest.mark.asyncio
async def test_unhealthy_model_blocks_startup(make_runtime):
    class Unhealthy(FakeModelProvider):
        async def health_check(self) -> bool:
            return False

    runtime = make_runtime(Unhealthy())
    agent = await runtime.create(_definition())
    with pytest.raises(RuntimeError, match="health check"):
        await runtime.start(agent)
    await runtime.close()
