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
from micro_agent.models import FakeModelConfig, FakeModelProvider, ModelResponse
from micro_agent.runtime import AgentRuntime
from micro_agent.security import AgentPolicy
from micro_agent.session import InMemorySessionProvider
from micro_agent.tools import Tool, ToolInputSchema, ToolMetadata, ToolOutputSchema, ToolResult
from runtimes.custom import CustomRuntime, CustomRuntimeConfig


def _custom(provider, **services) -> AgentRuntime:
    return CustomRuntime(CustomRuntimeConfig(model_provider=provider, **services))


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


class _ChargeTool(Tool):
    """A side-effecting tool that records how often it really ran."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(name="charge", description="Charge.", source="native")

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(parameters={"type": "object", "properties": {}})

    @property
    def output_schema(self) -> ToolOutputSchema:
        return ToolOutputSchema()

    async def execute(self, arguments):
        self.calls += 1
        return ToolResult(output={"charged": True})


class _RequestsThenAnswers(FakeModelProvider):
    """Requests ``tool`` on the first call, then answers with ``answer``."""

    def __init__(self, tool: str, answer: str = "all done") -> None:
        super().__init__(FakeModelConfig(response=answer))
        self.tool, self.answer, self.calls = tool, answer, 0

    async def generate(self, config, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(tool_requests=[{"name": self.tool, "arguments": {}}])
        return ModelResponse(content=self.answer)


def _definition(*, tools: bool = False, max_iterations: int | None = None, tool: str = "echo"):
    spec: dict = {
        "behavior": {"instructions": "Answer briefly."},
        "dependencies": {"model": {"ref": "conformance-model", "provider": "fake"}},
    }
    if tools:
        spec["dependencies"]["tools"] = [{"name": tool, "source": "native"}]
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


@pytest.mark.asyncio
async def test_policy_denied_tool_is_never_executed(make_runtime):
    tool = _ChargeTool()
    runtime = make_runtime(
        _RequestsThenAnswers("charge"),
        policy=AgentPolicy(denied_tools=["charge"]),
        tool_registry={"charge": tool},
    )
    response = await _run(runtime, _definition(tools=True, tool="charge"), AgentRequest(input={}))
    assert response.status == "success"
    assert tool.calls == 0
    (result,) = response.output["tool_results"]
    assert result["denied"] is True and "denied by policy" in result["error"]


async def _paused(runtime):
    """Run until the tool needs approval; return the agent and the pause response."""
    agent = await runtime.create(_definition(tools=True, tool="charge"))
    await runtime.start(agent)
    paused = await runtime.invoke(agent, AgentRequest(input={"go": 1}, session_id="approve-1"))
    return agent, paused


def _approval_runtime(make_runtime, tool, answer):
    return make_runtime(
        _RequestsThenAnswers("charge", answer),
        session_provider=InMemorySessionProvider(),
        policy=AgentPolicy(approval_required=True),
        tool_registry={"charge": tool},
    )


@pytest.mark.asyncio
async def test_a_tool_needing_approval_pauses_then_runs_once_when_approved(make_runtime):
    tool = _ChargeTool()
    runtime = _approval_runtime(make_runtime, tool, "charged ok")
    agent, paused = await _paused(runtime)
    try:
        assert paused.status == "approval_required"
        assert paused.metadata["pending_tools"] == ["charge"]
        assert tool.calls == 0  # nothing ran while waiting for the decision
        resumed = await runtime.invoke(
            agent,
            AgentRequest(
                input={},
                session_id="approve-1",
                continuation_id=paused.metadata["continuation_id"],
                approval_decision="approve",
            ),
        )
    finally:
        await runtime.close()
    assert resumed.status == "success"
    assert resumed.output["content"] == "charged ok"
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_a_denied_approval_never_runs_the_tool_and_the_model_still_answers(make_runtime):
    tool = _ChargeTool()
    runtime = _approval_runtime(make_runtime, tool, "understood")
    agent, paused = await _paused(runtime)
    try:
        resumed = await runtime.invoke(
            agent,
            AgentRequest(
                input={},
                session_id="approve-1",
                continuation_id=paused.metadata["continuation_id"],
                approval_decision="deny",
            ),
        )
    finally:
        await runtime.close()
    assert resumed.status == "success"
    assert resumed.output["content"] == "understood"
    assert tool.calls == 0
    assert any(r.get("denied") for r in resumed.output["tool_results"])


@pytest.mark.asyncio
async def test_an_unknown_continuation_is_rejected(make_runtime):
    from micro_agent.core import ContinuationNotFoundError

    runtime = make_runtime(FakeModelProvider(), session_provider=InMemorySessionProvider())
    agent = await runtime.create(_definition())
    try:
        await runtime.start(agent)
        with pytest.raises(ContinuationNotFoundError):
            await runtime.invoke(
                agent,
                AgentRequest(
                    input={},
                    session_id="x",
                    continuation_id="never-issued",
                    approval_decision="approve",
                ),
            )
    finally:
        await runtime.close()
