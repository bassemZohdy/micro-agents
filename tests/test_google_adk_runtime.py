"""Google ADK adapter tests using the runtime-neutral model provider seam."""

from __future__ import annotations

import pytest

from micro_agent.core import AgentRequest, ContinuationNotFoundError
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelProvider,
    ModelResponse,
)
from runtimes.google_adk import GoogleAdkError, GoogleAdkRuntime, GoogleAdkRuntimeConfig

pytest.importorskip("google.adk")

pytestmark = pytest.mark.adk


def _definition(
    *,
    provider: str | None = None,
    include_tool: bool = False,
    dependencies_extra: dict | None = None,
):
    model = {"ref": "test-model"}
    if provider is not None:
        model["provider"] = provider
    dependencies: dict[str, object] = {"model": model}
    if include_tool:
        dependencies["tools"] = [{"name": "echo", "source": "native"}]
    if dependencies_extra:
        dependencies.update(dependencies_extra)
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "adk-test-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Use the declared tools safely."},
                "dependencies": dependencies,
            },
        }
    )


class SequencedProvider(ModelProvider):
    """Provider double that emits one ADK tool call, then a final answer."""

    def capabilities(self):
        from micro_agent.models.model import ProviderCapabilities

        return ProviderCapabilities(tool_use=True)

    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[list[dict[str, object]]] = []

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
    ) -> ModelResponse:
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return ModelResponse(
                tool_requests=[{"name": "echo", "arguments": {"message": "from-adk"}}]
            )
        return ModelResponse(content="completed by ADK")

    async def health_check(self) -> bool:
        return True


class UnhealthyProvider(ModelProvider):
    async def generate(self, config, messages, tools=None):
        return ModelResponse(content="unreachable")

    async def health_check(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_constructs_native_adk_agent_and_invokes_provider():
    from google.adk.agents import LlmAgent

    from micro_agent.models import FakeModelConfig, FakeModelProvider

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(FakeModelConfig(response="hello from ADK"))
        )
    )
    agent = await runtime.create(_definition(include_tool=True))
    try:
        assert isinstance(agent._internal["adk_agent"], LlmAgent)
        assert agent._internal["adk_agent"].name == "micro_agent_adk_test_agent"
        assert len(agent._internal["adk_agent"].tools) == 1
        await runtime.start(agent)
        response = await runtime.invoke(
            agent,
            AgentRequest(input={"message": "hello"}, session_id="session-1"),
        )
        assert response.output["content"] == "hello from ADK"
        assert response.session_id == "session-1"
        assert response.metadata["runtime"] == "google-adk"
        session = await agent._internal["adk_session_service"].get_session(
            app_name=agent._internal["app_name"],
            user_id=agent._internal["user_id"],
            session_id="session-1",
        )
        assert session is not None
        assert session.events
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_tool_call_preserves_result_and_provider_tool_id():
    provider = SequencedProvider()
    runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider))
    agent = await runtime.create(_definition(include_tool=True))
    try:
        response = await runtime.invoke(agent, AgentRequest(input={"message": "run"}))
        assert response.output["content"] == "completed by ADK"
        assert response.output["tool_results"][0]["tool"] == "echo"
        assert response.output["tool_results"][0]["output"] == {"echoed": "from-adk"}
        assert provider.calls == 2
        assert provider.messages[1][-1]["role"] == "tool"
        assert provider.messages[1][-1]["tool_call_id"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_approval_continuation_uses_native_confirmation_flow():
    from micro_agent.security import AgentPolicy

    provider = SequencedProvider()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=provider,
            policy=AgentPolicy(approval_required=True),
        )
    )
    agent = await runtime.create(_definition(include_tool=True))
    try:
        await runtime.start(agent)
        pending = await runtime.invoke(
            agent,
            AgentRequest(input={"message": "run"}, session_id="approval-session"),
        )
        assert pending.status == "approval_required"
        assert pending.session_id == "approval-session"
        continuation_id = pending.metadata["continuation_id"]
        assert continuation_id
        assert pending.metadata["pending_tools"] == ["echo"]
        assert provider.calls == 1

        with pytest.raises(ContinuationNotFoundError):
            await runtime.invoke(
                agent,
                AgentRequest(
                    input={},
                    session_id="different-session",
                    continuation_id=continuation_id,
                    approval_decision="approve",
                ),
            )

        resumed = await runtime.invoke(
            agent,
            AgentRequest(
                input={},
                session_id="approval-session",
                continuation_id=continuation_id,
                approval_decision="approve",
            ),
        )
        assert resumed.status == "success"
        assert resumed.output["content"] == "completed by ADK"
        assert resumed.output["tool_results"][0]["output"] == {"echoed": "from-adk"}
        assert provider.calls == 2

        with pytest.raises(ContinuationNotFoundError):
            await runtime.invoke(
                agent,
                AgentRequest(
                    input={},
                    session_id="approval-session",
                    continuation_id=continuation_id,
                    approval_decision="approve",
                ),
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_start_checks_injected_provider_health():
    runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=UnhealthyProvider()))
    agent = await runtime.create(_definition())
    with pytest.raises(RuntimeError, match="health check"):
        await runtime.start(agent)
    await runtime.close()


def test_native_adk_rejects_non_google_provider_without_injected_model():
    runtime = GoogleAdkRuntime()
    with pytest.raises(GoogleAdkError, match="native ADK model"):
        # Creation is async only because the SPI is async; no provider call is made.
        import asyncio

        asyncio.run(runtime.create(_definition(provider="openai-compatible")))


class _TelemetryCapture:
    """Record a tool denial instead of executing the tool."""

    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[list[dict[str, object]]] = []

    async def generate(self, config, messages, tools=None):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return ModelResponse(
                tool_requests=[{"name": "echo", "arguments": {"message": "denied?"}}]
            )
        return ModelResponse(content="done")

    async def health_check(self) -> bool:
        return True

    def capabilities(self):
        from micro_agent.models.model import ProviderCapabilities

        return ProviderCapabilities(tool_use=True)


@pytest.mark.asyncio
async def test_memory_dependency_maps_to_adk_memory_service():
    from micro_agent.memory import InMemoryMemoryProvider, MemoryPolicy

    provider = FakeModelProvider()
    memory_provider = InMemoryMemoryProvider(MemoryPolicy(auto_store=True))
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=provider,
            memory_provider=memory_provider,
            memory_policy=MemoryPolicy(auto_store=True),
        )
    )
    definition = _definition(
        include_tool=True,
        dependencies_extra={"memory": {"ref": "agent-memory", "scope": "agent"}},
    )
    agent = await runtime.create(definition)
    try:
        assert runtime.capabilities().memory is True
        memory_service = agent._internal["adk_memory_service"]
        assert memory_service is not None
        await runtime.start(agent)
        await runtime.invoke(
            agent,
            AgentRequest(input={"message": "remember this"}, session_id="session-mem"),
        )
        entries = await memory_provider.list_entries()
        assert entries, "auto-store should persist session events through ADK memory"
        assert any("remember this" in str(entry.value) for entry in entries)

        response = await memory_service.search_memory(
            app_name=agent._internal["app_name"],
            user_id=agent._internal["user_id"],
            query="remember this",
        )
        assert response.memories
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_policy_denies_tool_before_execution():
    from micro_agent.memory import InMemoryMemoryProvider, MemoryPolicy
    from micro_agent.observability import Telemetry
    from micro_agent.security import AgentPolicy

    telemetry = Telemetry()
    provider = _TelemetryCapture()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=provider,
            policy=AgentPolicy(denied_tools=["echo"]),
            telemetry=telemetry,
            memory_provider=InMemoryMemoryProvider(MemoryPolicy()),
        )
    )
    agent = await runtime.create(_definition(include_tool=True))
    try:
        await runtime.start(agent)
        response = await runtime.invoke(agent, AgentRequest(input={"message": "go"}))
        denial = response.output["tool_results"][0]
        assert "denied by policy" in str(denial["output"])
        assert telemetry.metrics.get_metrics("policy_denials_total")
        # The denial is fed back to the model as the tool response.
        assert provider.calls == 2
        assert "denied by policy" in str(provider.messages[1][-1]["content"])
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_side_effect_policy_denies_tool():
    from micro_agent.security import AgentPolicy

    provider = _TelemetryCapture()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=provider,
            policy=AgentPolicy(side_effect_policy="deny"),
        )
    )
    agent = await runtime.create(_definition(include_tool=True))
    try:
        await runtime.start(agent)
        response = await runtime.invoke(agent, AgentRequest(input={"message": "go"}))
        denial = response.output["tool_results"][0]
        assert "denied by side-effect policy" in str(denial["output"])
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_declared_mcp_servers_map_to_adk_tools():
    from micro_agent.mcp import McpConnectionManager
    from micro_agent.mcp.client import FakeMcpClient
    from micro_agent.mcp.mcp import McpTool

    def factory(config):
        return FakeMcpClient(
            tools=[
                McpTool(
                    name="lookup",
                    description="Profile lookup",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            handlers={"lookup": lambda arguments: {"result": "ok"}},
        )

    manager = McpConnectionManager(client_factory=factory)

    class McpSequencedProvider(SequencedProvider):
        async def generate(self, config, messages, tools=None):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return ModelResponse(
                    tool_requests=[{"name": "profile_services_lookup", "arguments": {"q": "x"}}]
                )
            return ModelResponse(content="completed via MCP")

    mcp_provider = McpSequencedProvider()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(model_provider=mcp_provider, mcp_manager=manager)
    )
    definition = _definition(
        include_tool=True,
        dependencies_extra={
            "mcp_servers": [
                {
                    "ref": "profile-services",
                    "transport": "streamable-http",
                    "endpoint": "http://127.0.0.1:9000",
                }
            ]
        },
    )
    agent = await runtime.create(definition)
    try:
        assert runtime.capabilities().mcp is True
        await runtime.start(agent)
        adk_tool_names = [tool.name for tool in agent._internal["adk_agent"].tools]
        assert "profile_services_lookup" in adk_tool_names
        assert "mcp" in runtime.health_probes()
        response = await runtime.invoke(agent, AgentRequest(input={"message": "go"}))
        tool_results = response.output["tool_results"]
        lookup = next(result for result in tool_results if "lookup" in result["tool"])
        assert lookup["output"] == {"result": "ok"}
        assert mcp_provider.calls == 2
    finally:
        await runtime.close()
        assert manager.tools() == {}


@pytest.mark.asyncio
async def test_policy_denies_mcp_server_at_startup():
    from micro_agent.security import AgentPolicy

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(), policy=AgentPolicy(denied_mcps=["profile-services"])
        )
    )
    definition = _definition(
        dependencies_extra={
            "mcp_servers": [
                {
                    "ref": "profile-services",
                    "transport": "streamable-http",
                    "endpoint": "http://127.0.0.1:9000",
                }
            ]
        }
    )
    agent = await runtime.create(definition)
    try:
        with pytest.raises(PermissionError, match="profile-services"):
            await runtime.start(agent)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_telemetry_records_spans_and_metrics_for_invocation():
    from micro_agent.observability import Telemetry

    telemetry = Telemetry()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(FakeModelConfig(response="traced")),
            telemetry=telemetry,
        )
    )
    agent = await runtime.create(_definition())
    try:
        await runtime.start(agent)
        await runtime.invoke(
            agent,
            AgentRequest(input={"message": "hi"}, request_id="req-telemetry"),
        )
        spans = telemetry.get_spans()
        assert any(span.name == "agent.invoke" for span in spans)
        assert telemetry.metrics.get_metrics("agent_invocations_total")
        assert telemetry.metrics.get_metrics("agent_invocation_latency_ms")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unavailable_knowledge_source_fails_adk_startup():
    from micro_agent.knowledge import InMemoryKnowledgeRetriever

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(),
            knowledge_provider=InMemoryKnowledgeRetriever(),
        )
    )
    definition = _definition(
        dependencies_extra={"knowledge": [{"ref": "residency-rules"}]},
    )
    agent = await runtime.create(definition)
    try:
        with pytest.raises(RuntimeError, match="knowledge source 'residency-rules'"):
            await runtime.start(agent)
        assert "knowledge" in runtime.health_probes()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_available_knowledge_source_passes_adk_startup():
    from micro_agent.knowledge import InMemoryKnowledgeRetriever

    retriever = InMemoryKnowledgeRetriever(documents={"residency-rules": ["Rule one."]})
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(FakeModelConfig(response="ok")),
            knowledge_provider=retriever,
        )
    )
    definition = _definition(
        dependencies_extra={"knowledge": [{"ref": "residency-rules"}]},
    )
    agent = await runtime.create(definition)
    try:
        await runtime.start(agent)
        await runtime.invoke(agent, AgentRequest(input={"message": "hi"}))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_policy_denies_declared_skill_at_adk_startup():
    from micro_agent.security import AgentPolicy

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(),
            policy=AgentPolicy(denied_skills=["submit-renewal"]),
        )
    )
    definition = _definition(
        dependencies_extra={"skills": [{"id": "submit-renewal", "name": "Submit Renewal"}]},
    )
    agent = await runtime.create(definition)
    try:
        with pytest.raises(PermissionError, match="submit-renewal"):
            await runtime.start(agent)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_policy_denies_declared_model_at_adk_startup():
    from micro_agent.security import AgentPolicy

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(),
            policy=AgentPolicy(model_restrictions={"denied_models": ["test-model"]}),
        )
    )
    agent = await runtime.create(_definition())
    try:
        with pytest.raises(PermissionError, match="denied by policy"):
            await runtime.start(agent)
    finally:
        await runtime.close()
