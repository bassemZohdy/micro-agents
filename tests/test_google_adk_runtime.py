"""Google ADK adapter tests using the runtime-neutral model provider seam."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from micro_agent.core import AgentRequest, ContinuationNotFoundError
from micro_agent.definition import load_definition_from_dict
from micro_agent.knowledge import InMemoryKnowledgeRetriever
from micro_agent.memory import InMemoryMemoryProvider, MemoryEntry, MemoryPolicy
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ProviderCapabilities,
)
from runtimes.google_adk import GoogleAdkError, GoogleAdkRuntime, GoogleAdkRuntimeConfig
from runtimes.google_adk.runtime import (
    _adk_name,
    _approval_metadata,
    _entry_text,
    _event_text,
    _has_pending_confirmation,
    _messages_from_adk,
    _tools_from_adk,
)

pytest.importorskip("google.adk")

pytestmark = pytest.mark.adk


def _definition(
    *,
    provider: str | None = None,
    include_tool: bool = False,
    dependencies_extra: dict | None = None,
    output_contract: dict | None = None,
):
    model = {"ref": "test-model"}
    if provider is not None:
        model["provider"] = provider
    dependencies: dict[str, object] = {"model": model}
    if include_tool:
        dependencies["tools"] = [{"name": "echo", "source": "native"}]
    if dependencies_extra:
        dependencies.update(dependencies_extra)
    behavior = {"instructions": "Use the declared tools safely."}
    if output_contract is not None:
        behavior["output_contract"] = output_contract
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "adk-test-agent", "version": "1.0.0"},
            "spec": {
                "behavior": behavior,
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


class StructuredProvider(FakeModelProvider):
    """Provider double that accepts the runtime-neutral response format."""

    def capabilities(self):
        return ProviderCapabilities(tool_use=True, structured_output=True)


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
async def test_adk_runtime_streams_provider_deltas_and_final_response():
    from micro_agent.models import FakeModelConfig, FakeModelProvider

    provider = FakeModelProvider(FakeModelConfig(response="hello", stream_chunks=["hel", "lo"]))
    runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider))
    agent = await runtime.create(_definition())
    try:
        assert runtime.capabilities().streaming is True
        await runtime.start(agent)
        events = [
            event
            async for event in runtime.stream(
                agent,
                AgentRequest(input={"message": "stream"}, session_id="stream-session"),
            )
        ]
        assert [event.delta for event in events if event.delta] == ["hel", "lo"]
        final = events[-1].response
        assert final is not None
        assert final.output["content"] == "hello"
        assert final.session_id == "stream-session"
        assert provider.invocations
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_runtime_maps_output_contract_to_structured_provider():
    provider = StructuredProvider(FakeModelConfig(response='{"answer":"ok"}'))
    runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider))
    definition = _definition(
        output_contract={
            "parameters": [
                {"name": "answer", "type": "string", "required": True},
            ]
        }
    )
    agent = await runtime.create(definition)
    try:
        assert runtime.capabilities().structured_output is True
        await runtime.invoke(agent, AgentRequest(input={"message": "format"}))
        generation = provider.invocations[0]["config"].generation
        assert generation["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "micro_agent_output",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "additionalProperties": False,
                    "required": ["answer"],
                },
            },
        }
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
async def test_adk_read_only_tool_skips_side_effect_confirmation():
    from micro_agent.security import AgentPolicy

    provider = SequencedProvider()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=provider,
            policy=AgentPolicy(approval_required=True, side_effect_policy="deny"),
        )
    )
    definition = _definition(
        include_tool=True,
        dependencies_extra={
            "tools": [{"name": "echo", "source": "native", "side_effect": "read_only"}]
        },
    )
    agent = await runtime.create(definition)
    try:
        await runtime.start(agent)
        response = await runtime.invoke(
            agent,
            AgentRequest(input={"message": "run"}, session_id="read-only-session"),
        )
        assert response.status == "success"
        assert response.output["tool_results"][0]["output"] == {"echoed": "from-adk"}
        assert "continuation_id" not in response.metadata
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


@pytest.mark.asyncio
async def test_adk_runtime_retrieves_knowledge_before_model_call():
    from micro_agent.knowledge import InMemoryKnowledgeRetriever

    class RecordingKnowledgeProvider(ModelProvider):
        def __init__(self) -> None:
            self.messages: list[list[dict]] = []

        async def generate(self, config, messages, tools=None):
            self.messages.append(messages)
            return ModelResponse(content="knowledge answer")

        async def health_check(self) -> bool:
            return True

    provider = RecordingKnowledgeProvider()
    retriever = InMemoryKnowledgeRetriever(
        {"policy-kb": ["Refund policy allows returns for thirty days."]}
    )
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(model_provider=provider, knowledge_provider=retriever)
    )
    agent = await runtime.create(
        _definition(
            dependencies_extra={
                "knowledge": [
                    {
                        "ref": "policy-kb",
                        "max_results": 2,
                        "max_context_characters": 1200,
                    }
                ]
            }
        )
    )
    try:
        await runtime.start(agent)
        response = await runtime.invoke(
            agent, AgentRequest(input={"question": "What is the refund policy?"})
        )
        user_messages = [
            message for message in provider.messages[0] if message.get("role") == "user"
        ]
        assert user_messages
        assert "Refund policy allows returns for thirty days." in user_messages[-1]["content"]
        assert "untrusted reference data" in user_messages[-1]["content"]
        assert response.metadata["knowledge_entries"] == 1
    finally:
        await runtime.close()


@pytest.mark.parametrize(
    ("definition_name", "adk_name"),
    [
        ("residency-renewal", "micro_agent_residency_renewal"),
        ("9-agent", "micro_agent_agent_9_agent"),
        ("", "micro_agent_agent_"),
    ],
)
def test_adk_name_normalizes_runtime_identifiers(definition_name, adk_name):
    assert _adk_name(definition_name) == adk_name


def test_messages_from_adk_maps_text_tool_calls_and_responses():
    function_call = SimpleNamespace(id="call-1", name="echo", args={"message": "hi"})
    function_response = SimpleNamespace(id="call-1", name="echo", response={"echoed": "hi"})
    request = SimpleNamespace(
        contents=[
            SimpleNamespace(
                role="model",
                parts=[SimpleNamespace(text="I will check.", function_call=function_call)],
            ),
            SimpleNamespace(
                role="user",
                parts=[SimpleNamespace(function_response=function_response)],
            ),
        ]
    )

    assert _messages_from_adk(request) == [
        {
            "role": "assistant",
            "content": "I will check.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": '{"message": "hi"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "echo",
            "content": '{"echoed": "hi"}',
        },
    ]


def test_tools_from_adk_maps_declarations():
    declaration = SimpleNamespace(parameters_json_schema={"type": "object"})
    tool = SimpleNamespace(
        description="Echo text",
        _get_declaration=lambda: declaration,
    )

    assert _tools_from_adk(SimpleNamespace(tools_dict={"echo": tool})) == [
        {
            "name": "echo",
            "description": "Echo text",
            "input_schema": {"type": "object"},
        }
    ]


def test_adk_event_and_memory_entry_text_helpers():
    event = SimpleNamespace(
        content=SimpleNamespace(
            parts=[SimpleNamespace(text="hello "), SimpleNamespace(text="world")]
        )
    )

    assert _event_text(event) == "hello world"
    assert _entry_text(MemoryEntry(key="k", value={"text": "stored"}, scope="agent")) == "stored"
    assert _entry_text(MemoryEntry(key="k", value="plain", scope="agent")) == "plain"


def test_pending_confirmation_helper_rejects_answered_calls():
    function_call = SimpleNamespace(
        name="adk_request_confirmation",
        id="continuation-1",
    )
    function_response = SimpleNamespace(id="continuation-1")
    requested = [
        SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(function_call=function_call)])
        )
    ]

    assert _has_pending_confirmation(requested, "continuation-1") is True
    answered = requested + [
        SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(function_response=function_response)])
        )
    ]
    assert _has_pending_confirmation(answered, "continuation-1") is False


def test_approval_metadata_maps_confirmation_events():
    confirmation_call = SimpleNamespace(
        name="adk_request_confirmation",
        id="continuation-1",
        args={
            "originalFunctionCall": {"id": "call-1", "name": "echo"},
            "toolConfirmation": {"hint": "Approve echo", "payload": {"safe": True}},
        },
    )
    event = SimpleNamespace(
        actions=SimpleNamespace(requested_tool_confirmations={"call-1": {}}),
        content=SimpleNamespace(parts=[SimpleNamespace(function_call=confirmation_call)]),
    )

    assert _approval_metadata([event]) == {
        "continuation_id": "continuation-1",
        "pending_tools": ["echo"],
        "approval_hints": {"echo": "Approve echo"},
        "approval_payloads": {"echo": {"safe": True}},
    }


@pytest.mark.asyncio
async def test_adk_runtime_honors_request_timeout():
    never_finishes = asyncio.Event()

    class SlowRunner:
        async def run_async(self, **kwargs):
            await never_finishes.wait()
            if False:
                yield None

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(),
            runner_factory=lambda **kwargs: SlowRunner(),
        )
    )
    agent = await runtime.create(_definition())
    try:
        with pytest.raises(TimeoutError):
            await runtime.invoke(
                agent, AgentRequest(input={"message": "timeout"}, timeout_seconds=0.01)
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_runtime_stop_shutdown_and_close_release_resources():
    class ClosableRunner:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    runner = ClosableRunner()
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(),
            runner_factory=lambda **kwargs: runner,
        )
    )
    agent = await runtime.create(_definition())

    await runtime.start(agent)
    await runtime.stop(agent)
    await runtime.shutdown(agent)
    assert agent._internal is None
    await runtime.close()
    assert runner.closed is True


@pytest.mark.asyncio
async def test_adk_runtime_health_probes_cover_injected_dependencies():
    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(),
            memory_provider=InMemoryMemoryProvider(MemoryPolicy()),
            knowledge_provider=InMemoryKnowledgeRetriever(documents={"rules": ["A rule."]}),
        )
    )
    await runtime.create(_definition(dependencies_extra={"knowledge": [{"ref": "rules"}]}))
    try:
        probes = runtime.health_probes()
        assert set(probes) == {"model", "memory", "knowledge"}
        assert await probes["model"]() is True
        assert await probes["memory"]() is True
        assert await probes["knowledge"]() is True
    finally:
        await runtime.close()
