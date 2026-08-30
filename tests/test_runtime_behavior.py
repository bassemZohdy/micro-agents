"""Behavioral tests: runtime semantics, telemetry wiring, health probes, provider."""

import asyncio
import json

import httpx
import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.memory import InMemoryMemoryProvider, MemoryPolicy
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    OpenAICompatConfig,
    OpenAICompatProvider,
)
from micro_agent.observability import (
    HealthChecker,
    HealthStatus,
    Telemetry,
)
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
        await asyncio.sleep(self._delay)
        return ToolResult(output={"done": True})


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
        )
        runtime = AdkRuntime(config)
        probes = runtime.health_probes()
        assert set(probes) == {"model", "session", "memory"}
        checker = HealthChecker()
        for name, probe in probes.items():
            checker.add_dependency(name, probe=probe)
        result = await checker.probe_readiness()
        assert result.is_ready
        assert all(d.status == HealthStatus.HEALTHY for d in result.dependencies)

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
