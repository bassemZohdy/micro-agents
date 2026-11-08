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

    def __init__(self, name: str = "charge", side_effect: str = "unsafe") -> None:
        self.calls = 0
        self._name = name
        self._side_effect = side_effect

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name=self._name,
            description="Counts executions.",
            source="native",
            side_effect=self._side_effect,  # type: ignore[arg-type]
        )

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


def _definition(
    *,
    tools: bool = False,
    max_iterations: int | None = None,
    tool: str = "echo",
    **runtime_overrides,
):
    spec: dict = {
        "behavior": {"instructions": "Answer briefly."},
        "dependencies": {"model": {"ref": "conformance-model", "provider": "fake"}},
    }
    if tools:
        spec["dependencies"]["tools"] = [{"name": tool, "source": "native"}]
    if max_iterations is not None:
        runtime_overrides["max_iterations"] = max_iterations
    if runtime_overrides:
        spec["runtime"] = runtime_overrides
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


class _FailsOnceAfterTheToolRound(FakeModelProvider):
    """Calls ``lookup``, then fails the follow-up model call exactly once."""

    def __init__(self) -> None:
        super().__init__(FakeModelConfig(response="resumed answer"))
        self.calls = 0

    async def generate(self, config, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(tool_requests=[{"name": "lookup", "arguments": {}}])
        if self.calls == 2:
            raise RuntimeError("model went away")
        return ModelResponse(content="resumed answer")


@pytest.mark.asyncio
async def test_an_interrupted_invocation_resumes_from_its_checkpoint(make_runtime):
    from micro_agent.checkpoint import InMemoryCheckpointStore

    lookup = _ChargeTool("lookup", side_effect="read_only")
    provider = _FailsOnceAfterTheToolRound()
    sessions = InMemorySessionProvider()
    runtime = make_runtime(
        provider,
        session_provider=sessions,
        checkpoint_store=InMemoryCheckpointStore(),
        tool_registry={"lookup": lookup},
    )
    agent = await runtime.create(_definition(tools=True, tool="lookup"))
    try:
        await runtime.start(agent)
        with pytest.raises(RuntimeError, match="model went away"):
            await runtime.invoke(
                agent, AgentRequest(input={"q": 1}, session_id="ckpt-1", request_id="req-1")
            )
        assert lookup.calls == 1
        resumed = await runtime.invoke(
            agent, AgentRequest(input={}, session_id="ckpt-1", checkpoint_id="req-1")
        )
    finally:
        await runtime.close()
    assert resumed.status == "success"
    assert resumed.output["content"] == "resumed answer"
    assert lookup.calls == 1  # the completed tool round was not repeated
    assert [r["tool"] for r in resumed.output["tool_results"]] == ["lookup"]
    assert resumed.metadata["resumed_from_checkpoint"] == "req-1"


@pytest.mark.asyncio
async def test_resuming_an_unknown_checkpoint_is_rejected(make_runtime):
    from micro_agent.checkpoint import InMemoryCheckpointStore
    from micro_agent.core import CheckpointNotFoundError

    runtime = make_runtime(
        FakeModelProvider(),
        session_provider=InMemorySessionProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
    )
    agent = await runtime.create(_definition())
    try:
        await runtime.start(agent)
        with pytest.raises(CheckpointNotFoundError):
            await runtime.invoke(agent, AgentRequest(input={}, checkpoint_id="never-saved"))
    finally:
        await runtime.close()


# ---------------------------------------------------------------------------
# Model resilience, deadline and usage: identical under every runtime
# ---------------------------------------------------------------------------


class _FlakyProvider(FakeModelProvider):
    """Fails the first ``failures`` calls with a transient error, then answers."""

    def __init__(self, failures: int, answer: str = "recovered") -> None:
        super().__init__(FakeModelConfig(response=answer))
        self.failures, self.answer, self.calls = failures, answer, 0

    async def generate(self, config, messages, tools=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("connection reset")
        return ModelResponse(content=self.answer)


@pytest.mark.asyncio
async def test_a_transient_model_failure_is_retried_under_the_retry_policy(make_runtime):
    from micro_agent.observability import Telemetry

    telemetry = Telemetry()
    provider = _FlakyProvider(failures=2)
    response = await _run(
        make_runtime(provider, telemetry=telemetry),
        _definition(error_policy="retry", retry_max_attempts=3, retry_backoff_seconds=0),
        AgentRequest(input={}),
    )
    assert response.status == "success" and response.output["content"] == "recovered"
    assert provider.calls == 3
    assert telemetry.metrics.get_metrics("agent_retries_total")


@pytest.mark.asyncio
async def test_a_fatal_model_failure_follows_the_fallback_policy(make_runtime):
    provider = FakeModelProvider(FakeModelConfig(should_error=True, error_message="boom"))
    response = await _run(
        make_runtime(provider),
        _definition(error_policy="fallback"),
        AgentRequest(input={}),
    )
    assert response.status == "error"
    assert "boom" in (response.error or "")
    assert response.metadata["error_policy"] == "fallback"


@pytest.mark.asyncio
async def test_repeated_failures_open_the_circuit_breaker(make_runtime):
    from micro_agent.observability import Telemetry

    telemetry = Telemetry()
    provider = _FlakyProvider(failures=100)
    runtime = make_runtime(provider, telemetry=telemetry)
    definition = _definition(circuit_breaker_failures=2, circuit_breaker_cooldown_seconds=60)
    agent = await runtime.create(definition)
    try:
        await runtime.start(agent)
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await runtime.invoke(agent, AgentRequest(input={}))
        calls_when_open = provider.calls
        with pytest.raises(Exception, match="circuit breaker open"):
            await runtime.invoke(agent, AgentRequest(input={}))
    finally:
        await runtime.shutdown(agent)
        await runtime.close()
    assert provider.calls == calls_when_open  # the open circuit rejected before any model call
    assert telemetry.metrics.get_metrics("circuit_breaker_trips_total")


@pytest.mark.asyncio
async def test_the_invocation_deadline_bounds_a_slow_model(make_runtime):
    import asyncio

    class Slow(FakeModelProvider):
        async def generate(self, config, messages, tools=None):
            await asyncio.sleep(5)
            return ModelResponse(content="too late")

    with pytest.raises(TimeoutError):
        await _run(
            make_runtime(Slow()),
            _definition(),
            AgentRequest(input={}, timeout_seconds=0.2),
        )


@pytest.mark.asyncio
async def test_token_usage_is_reported_and_recorded(make_runtime):
    from micro_agent.observability import Telemetry

    class Metered(FakeModelProvider):
        async def generate(self, config, messages, tools=None):
            return ModelResponse(
                content="counted",
                usage={"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            )

    telemetry = Telemetry()
    response = await _run(
        make_runtime(Metered(), telemetry=telemetry),
        _definition(),
        AgentRequest(input={}),
    )
    assert response.metadata["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "total_tokens": 16,
    }
    assert telemetry.metrics.get_metrics("model_tokens_total")
    assert telemetry.metrics.get_metrics("model_latency_ms")


# ---------------------------------------------------------------------------
# Tool boundary: validation, idempotency, audit and telemetry
# ---------------------------------------------------------------------------


class _AmountTool(_ChargeTool):
    """A side-effecting tool whose schema requires ``amount``."""

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            parameters={
                "type": "object",
                "properties": {
                    "amount": {"type": "integer"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["amount"],
            }
        )


class _RequestsOncePerInvocation(FakeModelProvider):
    """Requests ``tool`` with ``arguments`` on every first model call of an invocation."""

    def __init__(self, tool: str, arguments: dict) -> None:
        super().__init__(FakeModelConfig(response="finished"))
        self.tool, self.arguments, self.calls = tool, arguments, 0

    async def generate(self, config, messages, tools=None):
        self.calls += 1
        if self.calls % 2 == 1:
            return ModelResponse(tool_requests=[{"name": self.tool, "arguments": self.arguments}])
        return ModelResponse(content="finished")


@pytest.mark.asyncio
async def test_invalid_tool_arguments_are_rejected_before_the_tool_runs(make_runtime):
    tool = _AmountTool()
    runtime = make_runtime(
        _RequestsThenAnswers("charge"),  # asks for the tool with no arguments at all
        tool_registry={"charge": tool},
    )
    response = await _run(runtime, _definition(tools=True, tool="charge"), AgentRequest(input={}))
    assert response.status == "success"
    assert tool.calls == 0
    (result,) = response.output["tool_results"]
    assert "missing required argument 'amount'" in result["error"]


@pytest.mark.asyncio
async def test_a_side_effect_runs_once_per_idempotency_key(make_runtime):
    from micro_agent.security import OperationRegistry

    tool = _AmountTool()
    runtime = make_runtime(
        _RequestsOncePerInvocation("charge", {"amount": 5, "idempotency_key": "order-1"}),
        tool_registry={"charge": tool},
        operation_registry=OperationRegistry(),
    )
    agent = await runtime.create(_definition(tools=True, tool="charge"))
    try:
        await runtime.start(agent)
        first = await runtime.invoke(agent, AgentRequest(input={"n": 1}))
        second = await runtime.invoke(agent, AgentRequest(input={"n": 2}))
    finally:
        await runtime.shutdown(agent)
        await runtime.close()
    assert first.status == second.status == "success"
    assert tool.calls == 1  # the second invocation reused the recorded operation


class _CollectingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record(self, event: str, **fields) -> None:
        self.events.append((event, fields))


@pytest.mark.asyncio
async def test_tool_calls_emit_the_same_audit_and_telemetry(make_runtime):
    from micro_agent.observability import Telemetry

    telemetry, audit = Telemetry(), _CollectingAudit()
    allowed = _ChargeTool("lookup", side_effect="read_only")
    runtime = make_runtime(
        _RequestsThenAnswers("lookup"),
        telemetry=telemetry,
        tool_registry={"lookup": allowed},
    )
    await _run(runtime, _definition(tools=True, tool="lookup"), AgentRequest(input={}))
    assert allowed.calls == 1
    assert telemetry.metrics.get_metrics("tool_calls_total")
    assert telemetry.metrics.get_metrics("tool_latency_ms")

    denied = _ChargeTool("charge")
    runtime = make_runtime(
        _RequestsThenAnswers("charge"),
        telemetry=telemetry,
        audit=audit,
        policy=AgentPolicy(denied_tools=["charge"]),
        tool_registry={"charge": denied},
    )
    await _run(runtime, _definition(tools=True, tool="charge"), AgentRequest(input={}))
    assert denied.calls == 0
    assert telemetry.metrics.get_metrics("policy_denials_total")
    (event, fields) = next(item for item in audit.events if item[0] == "policy.tool_denied")
    assert fields["tool"] == "charge"
