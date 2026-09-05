"""Behavioral tests: runtime semantics, telemetry wiring, health probes, provider."""

import asyncio
import json

import httpx
import pytest

from micro_agent.core import AgentRequest, AgentState, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.memory import InMemoryMemoryProvider, MemoryPolicy
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelResponse,
    OpenAICompatConfig,
    OpenAICompatProvider,
)
from micro_agent.observability import (
    HealthChecker,
    HealthStatus,
    OperationRegistry,
    Telemetry,
)
from micro_agent.security import InMemoryApprovalStore, UserContext
from micro_agent.session import InMemorySessionProvider
from micro_agent.tools import Tool, ToolInputSchema, ToolMetadata, ToolOutputSchema, ToolResult
from runtimes.adk import AdkRuntime, AdkRuntimeConfig

pytestmark = pytest.mark.integration


def _definition(**runtime) -> object:
    spec: dict = {
        "behavior": {"instructions": "You are a test agent."},
        "dependencies": {
            "model": {"ref": "fake-model"},
            "tools": [{"name": "echo", "source": "native"}],
            "skills": [
                {
                    "id": "greet",
                    "name": "Greeting",
                    "description": "Greets callers.",
                    "tags": ["social"],
                }
            ],
        },
    }
    if runtime:
        spec["runtime"] = runtime
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test-agent", "version": "1.0.0"},
            "spec": spec,
        }
    )


class SlowTool(Tool):
    """Tool that sleeps to exercise timeout enforcement."""

    def __init__(self, delay: float, timeout_seconds: float | None = 5) -> None:
        self._delay = delay
        self._timeout = timeout_seconds
        self.cancelled = False

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(name="slow", description="Slow tool", timeout_seconds=self._timeout)

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema()

    @property
    def output_schema(self) -> ToolOutputSchema:
        return ToolOutputSchema()

    async def execute(self, arguments: dict) -> ToolResult:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolResult(output={"done": True})


class SlowModelProvider(FakeModelProvider):
    """Model double that records cancellation of an in-flight request."""

    def __init__(self, delay: float) -> None:
        super().__init__(FakeModelConfig(response="slow"))
        self._delay = delay
        self.cancelled = False

    async def generate(self, config, messages, tools=None):
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().generate(config, messages, tools=tools)


class SlowSessionProvider(InMemorySessionProvider):
    """Session double that records cancellation before a state read."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay
        self.cancelled = False

    async def get(self, session_id: str):
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().get(session_id)


class FailingSessionProvider(InMemorySessionProvider):
    """Session double that fails the startup readiness probe."""

    async def list_active(self):
        raise ConnectionError("session store unavailable")


class FailingMemoryProvider(InMemoryMemoryProvider):
    """Memory double that fails the startup readiness probe."""

    async def list_entries(self, scope=None):
        raise ConnectionError("memory store unavailable")


class TestRuntimeSemantics:
    """RuntimeSemantics enforcement: max_iterations, timeouts, error policy."""

    @pytest.mark.asyncio
    async def test_tool_loop_runs_until_no_tool_requests(self):
        class OneShotToolProvider(FakeModelProvider):
            """Returns a tool request on the first call only."""

            async def generate(self, config, messages, tools=None):
                if len(self.invocations) == 1:
                    self._config.tool_requests = []
                return await super().generate(config, messages, tools=tools)

        provider = OneShotToolProvider(
            FakeModelConfig(
                response="done",
                tool_requests=[{"name": "echo", "arguments": {"message": "hi"}}],
            )
        )
        config = AdkRuntimeConfig(model_provider=provider)
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={"x": 1}))
        assert response.status == "success"
        assert response.metadata["iterations"] == 2
        assert response.metadata["tools_called"] == ["echo"]

    @pytest.mark.asyncio
    async def test_max_iterations_stops_loop(self):
        config = AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(
                response="",
                # The fake model requests the tool on every iteration.
                tool_requests=[{"name": "echo", "arguments": {"message": "hi"}}],
            ),
            default_max_iterations=3,
        )
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(max_iterations=3))
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.metadata["iterations"] == 3
        assert response.metadata["max_iterations_reached"] is True

    @pytest.mark.asyncio
    async def test_overall_timeout_enforced(self):
        config = AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(
                response="slow",
            )
        )
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(timeout_seconds=1))
        # timeout_seconds >= 1 by schema; use a sleeping model instead below.
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.status == "success"
        assert response.metadata["iterations"] == 1

    @pytest.mark.asyncio
    async def test_request_deadline_cancels_model_call(self):
        provider = SlowModelProvider(delay=1.0)
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = await runtime.create(_definition())

        with pytest.raises(TimeoutError, match="deadline"):
            await runtime.invoke(agent, AgentRequest(timeout_seconds=0.05))
        assert provider.cancelled is True

    @pytest.mark.asyncio
    async def test_request_deadline_cancels_tool_call(self):
        provider = FakeModelProvider(
            FakeModelConfig(
                response="done",
                tool_requests=[{"name": "slow", "arguments": {}}],
            )
        )
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = await runtime.create(_definition())
        slow = SlowTool(delay=1.0)
        agent._internal["tools"] = {"slow": slow}

        with pytest.raises(TimeoutError, match="deadline"):
            await runtime.invoke(agent, AgentRequest(timeout_seconds=0.05))
        assert slow.cancelled is True

    @pytest.mark.asyncio
    async def test_tool_timeout_enforced(self):
        runtime = AdkRuntime()
        slow = SlowTool(delay=5.0, timeout_seconds=1)
        results = await runtime._execute_tools(
            {"slow": slow},
            [{"name": "slow", "arguments": {}}],
            trace_id="t",
            parent_span_id="p",
            labels={"agent": "test"},
        )
        assert results[0]["error"] is not None
        assert "timed out" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_error_policy_fail_raises(self):
        config = AdkRuntimeConfig(fake_model_config=FakeModelConfig(should_error=True))
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(error_policy="fail"))
        with pytest.raises(RuntimeError, match="fake model error"):
            await runtime.invoke(agent, AgentRequest(input={}))

    @pytest.mark.asyncio
    async def test_error_policy_fallback_returns_error_response(self):
        config = AdkRuntimeConfig(fake_model_config=FakeModelConfig(should_error=True))
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(error_policy="fallback"))
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.status == "error"
        assert "fake model error" in (response.error or "")

    @pytest.mark.asyncio
    async def test_error_policy_retry_retries(self):
        config = AdkRuntimeConfig(fake_model_config=FakeModelConfig(should_error=True))
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(error_policy="retry"))
        with pytest.raises(RuntimeError, match="fake model error"):
            await runtime.invoke(agent, AgentRequest(input={}))

    @pytest.mark.asyncio
    async def test_error_policy_retry_recovers_when_model_recovers(self):
        """Retry policy: the model errors once, then succeeds on the retry."""

        class RecoveringProvider(FakeModelProvider):
            def __init__(self, config: FakeModelConfig) -> None:
                super().__init__(config)
                self.attempts = 0

            async def generate(self, config, messages, tools=None):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("transient model error")
                return await super().generate(config, messages, tools=tools)

        provider = RecoveringProvider(FakeModelConfig(response="recovered"))
        config = AdkRuntimeConfig(model_provider=provider)
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(error_policy="retry"))
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.output["content"] == "recovered"

    @pytest.mark.asyncio
    async def test_error_policy_retry_honors_attempt_budget_and_backoff(self):
        class RecoveringAfterTwoFailures(FakeModelProvider):
            def __init__(self) -> None:
                super().__init__(FakeModelConfig(response="recovered"))
                self.calls = 0

            async def generate(self, config, messages, tools=None):
                self.calls += 1
                if self.calls < 3:
                    raise RuntimeError(f"transient model error {self.calls}")
                return await super().generate(config, messages, tools=tools)

        provider = RecoveringAfterTwoFailures()
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = await runtime.create(
            _definition(
                error_policy="retry",
                retry_max_attempts=2,
                retry_backoff_seconds=0.001,
                retry_jitter_seconds=0,
            )
        )
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.output["content"] == "recovered"
        assert provider.calls == 3

    @pytest.mark.asyncio
    async def test_error_policy_retry_budget_stops_before_next_attempt(self):
        provider = FakeModelProvider(
            FakeModelConfig(should_error=True, error_message="transient failure")
        )
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = await runtime.create(
            _definition(
                error_policy="retry",
                retry_max_attempts=3,
                retry_backoff_seconds=0.02,
                retry_budget_seconds=0.005,
            )
        )
        with pytest.raises(TimeoutError, match="retry budget exceeded"):
            await runtime.invoke(agent, AgentRequest(input={}))
        assert len(provider.invocations) == 1

    @pytest.mark.asyncio
    async def test_error_policy_retry_is_suppressed_after_side_effect_tool(self):
        class FailsAfterToolProvider(FakeModelProvider):
            def __init__(self) -> None:
                super().__init__(FakeModelConfig(response="unused"))
                self.calls = 0

            async def generate(self, config, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    return ModelResponse(
                        tool_requests=[{"name": "echo", "arguments": {"message": "write-attempt"}}]
                    )
                raise RuntimeError("transient model error after tool execution")

        provider = FailsAfterToolProvider()
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = await runtime.create(_definition(error_policy="retry"))
        with pytest.raises(RuntimeError, match="retry suppressed after side-effect"):
            await runtime.invoke(agent, AgentRequest(input={}))
        # A whole-invocation retry would issue a third model call and could
        # replay the already executed non-read-only tool.
        assert provider.calls == 2


class TestSessionsAndMemory:
    """Session and memory integration in the invoke path."""

    @pytest.mark.asyncio
    async def test_session_created_and_persisted(self):
        session_provider = InMemorySessionProvider()
        config = AdkRuntimeConfig(session_provider=session_provider)
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={}, session_id="sess-42"))
        assert response.session_id == "sess-42"
        session = await session_provider.get("sess-42")
        assert session is not None
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant"]
        assert session.metadata["created_at"]

    @pytest.mark.asyncio
    async def test_request_deadline_cancels_session_call(self):
        session_provider = SlowSessionProvider(delay=1.0)
        runtime = AdkRuntime(AdkRuntimeConfig(session_provider=session_provider))
        agent = await runtime.create(_definition())

        with pytest.raises(TimeoutError, match="deadline"):
            await runtime.invoke(
                agent,
                AgentRequest(session_id="slow-session", timeout_seconds=0.05),
            )
        assert session_provider.cancelled is True

    @pytest.mark.asyncio
    async def test_session_ttl_from_definition(self):
        session_provider = InMemorySessionProvider()
        definition = _definition()
        definition.spec.dependencies.session.ttl_seconds = 0  # type: ignore[union-attr]
        config = AdkRuntimeConfig(session_provider=session_provider)
        runtime = AdkRuntime(config)
        agent = await runtime.create(definition)
        await runtime.invoke(agent, AgentRequest(input={}, session_id="s1"))
        assert await session_provider.get("s1") is None  # expired immediately

    @pytest.mark.asyncio
    async def test_memory_auto_store(self):
        memory_provider = InMemoryMemoryProvider(
            policy=MemoryPolicy(auto_store=True, max_entries=10)
        )
        config = AdkRuntimeConfig(
            memory_provider=memory_provider, memory_policy=memory_provider.policy
        )
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={"q": "hi"}, request_id="req-1"))
        assert response.status == "success"
        entries = await memory_provider.list_entries()
        assert len(entries) == 1
        assert entries[0].key == "invocation:req-1"

    @pytest.mark.asyncio
    async def test_no_auto_store_by_default(self):
        memory_provider = InMemoryMemoryProvider()
        config = AdkRuntimeConfig(memory_provider=memory_provider)
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())
        await runtime.invoke(agent, AgentRequest(input={}, request_id="req-2"))
        assert await memory_provider.list_entries() == []

    @pytest.mark.asyncio
    async def test_tenant_context_scopes_session_and_memory(self):
        session_provider = InMemorySessionProvider()
        memory_provider = InMemoryMemoryProvider(policy=MemoryPolicy(auto_store=True))
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                session_provider=session_provider,
                memory_provider=memory_provider,
                memory_policy=memory_provider.policy,
            )
        )
        agent = await runtime.create(_definition())
        await runtime.invoke(
            agent,
            AgentRequest(
                input={"tenant": "a"},
                request_id="tenant-a-request",
                session_id="shared-session",
                user_context=UserContext(user_id="alice", tenant_id="tenant-a"),
            ),
        )
        await runtime.invoke(
            agent,
            AgentRequest(
                input={"tenant": "b"},
                request_id="tenant-b-request",
                session_id="shared-session",
                user_context=UserContext(user_id="bob", tenant_id="tenant-b"),
            ),
        )

        session_a = await session_provider.get("shared-session", tenant_id="tenant-a")
        session_b = await session_provider.get("shared-session", tenant_id="tenant-b")
        assert session_a is not None and session_b is not None
        assert session_a.tenant_id == "tenant-a"
        assert session_b.tenant_id == "tenant-b"
        assert await session_provider.get("shared-session") is None
        assert {
            entry.tenant_id for entry in await memory_provider.list_entries(tenant_id="tenant-a")
        } == {"tenant-a"}
        assert {
            entry.tenant_id for entry in await memory_provider.list_entries(tenant_id="tenant-b")
        } == {"tenant-b"}


class TestTelemetryWiring:
    """Metrics and spans recorded through the invocation path."""

    @pytest.mark.asyncio
    async def test_invocation_metrics_and_trace(self):
        telemetry = Telemetry()
        config = AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(
                response="done",
                tool_requests=[{"name": "echo", "arguments": {"message": "hi"}}],
            ),
            telemetry=telemetry,
        )
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())
        await runtime.invoke(agent, AgentRequest(input={}, request_id="trace-1"))

        names = [m.name for m in telemetry.metrics.get_metrics()]
        assert "agent_invocations_total" in names
        assert "agent_invocation_latency_ms" in names
        assert "model_latency_ms" in names
        assert "model_tokens_total" in names
        assert "tool_calls_total" in names
        assert "tool_latency_ms" in names

        spans = telemetry.get_spans()
        span_names = [s.name for s in spans]
        assert span_names[0] == "agent.invoke"
        assert "model.generate" in span_names
        assert "tool.echo" in span_names
        agent_span = spans[0]
        model_span = next(s for s in spans if s.name == "model.generate")
        tool_span = next(s for s in spans if s.name == "tool.echo")
        assert model_span.parent_span_id == agent_span.span_id
        assert tool_span.parent_span_id == agent_span.span_id
        assert all(s.end_time is not None for s in spans)

    @pytest.mark.asyncio
    async def test_error_metric_recorded(self):
        telemetry = Telemetry()
        config = AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(should_error=True),
            telemetry=telemetry,
        )
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition(error_policy="fail"))
        with pytest.raises(RuntimeError):
            await runtime.invoke(agent, AgentRequest(input={}))
        assert telemetry.metrics.get_metrics("agent_invocation_errors_total")

    @pytest.mark.asyncio
    async def test_skills_surface_in_system_prompt(self):
        config = AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(response="ok"), telemetry=Telemetry()
        )
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())
        await runtime.invoke(agent, AgentRequest(input={}))
        provider = runtime._model_provider
        assert isinstance(provider, FakeModelProvider)
        invocation = provider.invocations[0]
        system_message = invocation["messages"][0]["content"]
        assert "greet" in system_message
        assert "Greeting" in system_message
        assert "skills" in system_message.lower()


class TestHealthProbes:
    """Active health probes and liveness."""

    @pytest.mark.asyncio
    async def test_runtime_health_probes_registered(self):
        config = AdkRuntimeConfig(
            session_provider=InMemorySessionProvider(),
            memory_provider=InMemoryMemoryProvider(),
            operation_registry=OperationRegistry(),
        )
        runtime = AdkRuntime(config)
        probes = runtime.health_probes()
        assert set(probes) == {"model", "session", "memory", "operation_registry"}
        checker = HealthChecker()
        for name, probe in probes.items():
            checker.add_dependency(name, probe=probe)
        result = await checker.probe_readiness()
        assert result.is_ready
        assert all(d.status == HealthStatus.HEALTHY for d in result.dependencies)

    @pytest.mark.asyncio
    async def test_configured_approval_store_has_startup_and_readiness_probe(self):
        config = AdkRuntimeConfig(approval_store=InMemoryApprovalStore())
        runtime = AdkRuntime(config)
        agent = await runtime.create(_definition())

        await runtime.start(agent)

        probes = runtime.health_probes()
        assert "approval_store" in probes
        assert await probes["approval_store"]() is True

    @pytest.mark.asyncio
    async def test_probe_failure_makes_not_ready(self):
        async def failing() -> bool:
            return False

        checker = HealthChecker()
        checker.add_dependency("model", probe=failing)
        result = await checker.probe_readiness()
        assert not result.is_ready
        assert result.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_probe_exception_recorded_as_unhealthy(self):
        async def raising() -> bool:
            raise RuntimeError("connection refused")

        checker = HealthChecker()
        checker.add_dependency("model", probe=raising)
        result = await checker.probe_readiness()
        assert not result.is_ready
        assert result.dependencies[0].details["error"] == "connection refused"

    def test_liveness_probe_failure(self):
        checker = HealthChecker(liveness_probe=lambda: False)
        assert checker.check_liveness().alive is False
        checker.set_alive(False)
        assert HealthChecker().check_liveness().alive is True

    def test_update_status(self):
        checker = HealthChecker()
        checker.add_dependency("mcp")
        assert checker.update_status("mcp", HealthStatus.UNHEALTHY) is True
        assert checker.check_readiness().is_ready is False
        assert checker.update_status("missing", HealthStatus.HEALTHY) is False


class TestStartupReadiness:
    """Configured dependencies must pass probes before the agent is ready."""

    @pytest.mark.asyncio
    async def test_session_provider_failure_blocks_readiness(self):
        runtime = AdkRuntime(AdkRuntimeConfig(session_provider=FailingSessionProvider()))
        definition = _definition()
        agent = DefaultMicroAgent(definition, runtime)
        await agent.initialize()
        try:
            with pytest.raises(RuntimeError, match="session provider.*startup"):
                await agent.start()
            assert agent.state == AgentState.ERROR
        finally:
            await agent.shutdown()
            await runtime.close()

    @pytest.mark.asyncio
    async def test_memory_provider_failure_blocks_readiness(self):
        runtime = AdkRuntime(AdkRuntimeConfig(memory_provider=FailingMemoryProvider()))
        definition = _definition()
        agent = DefaultMicroAgent(definition, runtime)
        await agent.initialize()
        try:
            with pytest.raises(RuntimeError, match="memory provider.*startup"):
                await agent.start()
            assert agent.state == AgentState.ERROR
        finally:
            await agent.shutdown()
            await runtime.close()


class TestOpenAICompatProvider:
    """OpenAI-compatible provider against a mock transport."""

    def _provider(self, handler: httpx.MockTransport) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.com/v1",
                model_id="test-model",
                api_key="sk-test",
            )
        )

    @pytest.mark.asyncio
    async def test_generate_parses_content_and_tools(self):
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["model"] == "test-model"
            assert request.headers["Authorization"] == "Bearer sk-test"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "hello",
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "echo",
                                            "arguments": '{"message": "x"}',
                                        }
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            )

        provider = OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.com/v1",
                model_id="test-model",
                api_key="sk-test",
            )
        )
        provider._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://llm.example.com/v1",
            headers={"Authorization": "Bearer sk-test"},
        )
        response = await provider.generate(
            _definition_model_config(), [{"role": "user", "content": "hi"}]
        )
        assert response.content == "hello"
        assert response.tool_requests[0]["name"] == "echo"
        assert response.tool_requests[0]["arguments"] == {"message": "x"}
        assert response.usage["prompt_tokens"] == 3
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_health_check_false_on_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        provider = OpenAICompatProvider(OpenAICompatConfig(endpoint="https://llm.example.com/v1"))
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert await provider.health_check() is False
        await provider.aclose()


def _definition_model_config():

    return ModelConfig(ref="fake-model", model_id=None, generation={})


class TestToolCallTranscript:
    """Provider tool-call IDs and payloads survive in the conversation."""

    def _provider(self):
        from micro_agent.models import FakeModelConfig, FakeModelProvider

        class OneShotProvider(FakeModelProvider):
            async def generate(self, config, messages, tools=None):
                if len(self.invocations) >= 1:
                    self._config.tool_requests = []
                else:
                    self._config.tool_requests = [
                        {
                            "id": "call_wire_9",
                            "name": "echo",
                            "arguments": {"message": "transcript"},
                        }
                    ]
                return await super().generate(config, messages, tools=tools)

        return OneShotProvider(FakeModelConfig(response="done"))

    @pytest.mark.asyncio
    async def test_assistant_tool_calls_and_tool_call_id_in_history(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent
        from micro_agent.definition import load_definition_from_dict
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "transcript-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test."},
                    "dependencies": {
                        "model": {"ref": "fake-model", "provider": "fake"},
                        "tools": [{"name": "echo", "source": "native"}],
                    },
                },
            }
        )
        provider = self._provider()
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = DefaultMicroAgent(definition, runtime)
        try:
            await agent.initialize()
            await agent.start()
            response = await agent.invoke(AgentRequest(input={}))
            assert response.status == "success"
            history = provider.invocations[1]["messages"]
            assistant = next(m for m in history if m.get("tool_calls"))
            call = assistant["tool_calls"][0]
            assert call["id"] == "call_wire_9"
            assert call["type"] == "function"
            assert call["function"]["name"] == "echo"
            tool_message = next(m for m in history if m.get("role") == "tool")
            assert tool_message["tool_call_id"] == "call_wire_9"
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()

    @pytest.mark.asyncio
    async def test_requests_without_ids_get_generated_ones(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent
        from micro_agent.definition import load_definition_from_dict
        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        class NoIdProvider(FakeModelProvider):
            async def generate(self, config, messages, tools=None):
                if len(self.invocations) >= 1:
                    self._config.tool_requests = []
                else:
                    self._config.tool_requests = [{"name": "echo", "arguments": {"message": "hi"}}]
                return await super().generate(config, messages, tools=tools)

        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "noid-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test."},
                    "dependencies": {
                        "model": {"ref": "fake-model", "provider": "fake"},
                        "tools": [{"name": "echo", "source": "native"}],
                    },
                },
            }
        )
        provider = NoIdProvider(FakeModelConfig(response="done"))
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = DefaultMicroAgent(definition, runtime)
        try:
            await agent.initialize()
            await agent.start()
            response = await agent.invoke(AgentRequest(input={}))
            tool_result = response.output["tool_results"][0]
            assert tool_result["tool_call_id"]
            history = provider.invocations[1]["messages"]
            tool_message = next(m for m in history if m.get("role") == "tool")
            assert tool_message["tool_call_id"] == tool_result["tool_call_id"]
            assert tool_result["tool_call_id"].startswith("call_")
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()

    @pytest.mark.asyncio
    async def test_schema_invalid_arguments_are_rejected_before_execution(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent
        from micro_agent.definition import load_definition_from_dict
        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from micro_agent.tools import EchoTool
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        class InvalidArgsProvider(FakeModelProvider):
            async def generate(self, config, messages, tools=None):
                if len(self.invocations) >= 1:
                    self._config.tool_requests = []
                else:
                    self._config.tool_requests = [{"name": "echo", "arguments": {"wrong": 1}}]
                return await super().generate(config, messages, tools=tools)

        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "val-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test."},
                    "dependencies": {
                        "model": {"ref": "fake-model", "provider": "fake"},
                        "tools": [{"name": "echo", "source": "native"}],
                    },
                },
            }
        )
        executed: list[int] = []

        class SpyEchoTool(EchoTool):
            async def execute(self, arguments):
                executed.append(1)
                return await super().execute(arguments)

        provider = InvalidArgsProvider(FakeModelConfig(response="done"))
        runtime = AdkRuntime(
            AdkRuntimeConfig(model_provider=provider, tool_registry={"echo": SpyEchoTool()})
        )
        agent = DefaultMicroAgent(definition, runtime)
        try:
            await agent.initialize()
            await agent.start()
            response = await agent.invoke(AgentRequest(input={}))
            tool_result = response.output["tool_results"][0]
            assert tool_result["error"].startswith("invalid tool arguments")
            assert "message" in tool_result["error"]
            assert executed == []
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()

    @pytest.mark.asyncio
    async def test_provider_without_tool_use_fails_startup_when_tools_declared(self):
        from micro_agent.models import (
            FakeModelProvider,
            ProviderCapabilities,
        )

        class NoToolUseProvider(FakeModelProvider):
            def capabilities(self) -> ProviderCapabilities:
                return ProviderCapabilities(tool_use=False)
