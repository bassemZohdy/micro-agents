"""Policy enforcement and safe side effects in the runtime invoke path."""

import pytest

from micro_agent.core import AgentRequest, ContinuationNotFoundError
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelConfig, FakeModelProvider
from micro_agent.security import (
    AgentPolicy,
    OperationRegistry,
    RedisOperationRegistry,
    RetryClassification,
    UserContext,
    build_security_context,
)
from runtimes.adk import AdkRuntime, AdkRuntimeConfig
from tests.fake_redis import FakeRedis, FakeRedisBackend

pytestmark = pytest.mark.integration


def _definition(**deps) -> object:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "policy-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "tools": [{"name": "echo", "source": "native"}],
                    **deps,
                },
                "security": {
                    "credential_refs": ["residency-api-key"],
                    "policy_refs": ["residency-access-policy"],
                },
            },
        }
    )


class OneShotToolProvider(FakeModelProvider):
    """Returns a tool request on the first call only."""

    async def generate(self, config, messages, tools=None):
        if len(self.invocations) == 1:
            self._config.tool_requests = []
        return await super().generate(config, messages, tools=tools)


class RecordingOperationRegistry(OperationRegistry):
    """Capture operation metadata while retaining local deduplication behavior."""

    def __init__(self):
        super().__init__()
        self.operations = []

    def claim(self, operation):
        self.operations.append(operation)
        return super().claim(operation)


def _echo_runtime(policy: AgentPolicy | None = None, **config_kwargs) -> AdkRuntime:
    provider = OneShotToolProvider(
        FakeModelConfig(
            response="done",
            tool_requests=[{"name": "echo", "arguments": {"message": "hi"}}],
        )
    )
    return AdkRuntime(
        AdkRuntimeConfig(
            model_provider=provider,
            policy=policy,
            **config_kwargs,
        )
    )


class TestSecurityContextLoading:
    """Definition security refs load into a SecurityContext."""

    def test_policy_and_credential_refs_loaded(self):
        context = build_security_context(_definition())
        assert context.policy_refs == ["residency-access-policy"]
        assert context.credential_refs == ["residency-api-key"]
        assert context.agent_identity.agent_name == "policy-agent"

    @pytest.mark.asyncio
    async def test_runtime_agent_carries_security_context(self):
        runtime = _echo_runtime()
        agent = await runtime.create(_definition())
        assert agent._internal["security_context"].policy_refs == ["residency-access-policy"]


class TestToolPolicyEnforcement:
    """Deterministic platform policy applied outside prompt instructions."""

    @pytest.mark.asyncio
    async def test_denied_tool_not_executed(self):
        policy = AgentPolicy(denied_tools=["echo"])
        runtime = _echo_runtime(policy=policy)
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={}))
        tool_results = response.output["tool_results"]
        assert tool_results[0]["error"] is not None
        assert "denied by policy" in tool_results[0]["error"]
        # The model was called, but the tool itself never ran.
        assert response.metadata["tools_called"] == []

    @pytest.mark.asyncio
    async def test_side_effect_policy_deny_blocks_tools(self):
        policy = AgentPolicy(side_effect_policy="deny")
        runtime = _echo_runtime(policy=policy)
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert "side-effect policy" in (response.output["tool_results"][0]["error"] or "")

    @pytest.mark.asyncio
    async def test_approval_required_pauses_with_continuation(self):
        from micro_agent.security import InMemoryApprovalStore

        policy = AgentPolicy(approval_required=True)
        runtime = _echo_runtime(policy=policy, approval_store=InMemoryApprovalStore())
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.status == "approval_required"
        assert response.metadata["continuation_id"]
        assert response.metadata["pending_tools"] == ["echo"]
        assert response.output["tool_results"] == []
        # The pause is not a completed invocation: nothing was executed and
        # nothing was persisted to the session.
        assert await runtime._approval_store.get(response.metadata["continuation_id"])

    @pytest.mark.asyncio
    async def test_read_only_tool_bypasses_side_effect_approval(self):
        policy = AgentPolicy(approval_required=True, side_effect_policy="deny")
        registry = RecordingOperationRegistry()
        runtime = _echo_runtime(policy=policy, operation_registry=registry)
        agent = await runtime.create(
            _definition(tools=[{"name": "echo", "source": "native", "side_effect": "read_only"}])
        )
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.status == "success"
        assert response.output["tool_results"][0]["output"] == {"echoed": "hi"}
        assert response.metadata["tools_called"] == ["echo"]
        assert registry.operations == []

    @pytest.mark.asyncio
    async def test_idempotent_tool_marks_operation_retry_classification(self):
        registry = RecordingOperationRegistry()
        runtime = _echo_runtime(operation_registry=registry)
        agent = await runtime.create(
            _definition(tools=[{"name": "echo", "source": "native", "side_effect": "idempotent"}])
        )
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.status == "success"
        assert registry.operations
        assert registry.operations[0].retry_classification is RetryClassification.IDEMPOTENT

    @pytest.mark.asyncio
    async def test_approved_continuation_executes_and_completes(self):
        from micro_agent.security import InMemoryApprovalStore

        policy = AgentPolicy(approval_required=True)
        runtime = _echo_runtime(policy=policy, approval_store=InMemoryApprovalStore())
        agent = await runtime.create(_definition())
        paused = await runtime.invoke(agent, AgentRequest(input={}))
        continuation_id = paused.metadata["continuation_id"]
        response = await runtime.invoke(
            agent,
            AgentRequest(input={}, continuation_id=continuation_id, approval_decision="approve"),
        )
        assert response.status == "success"
        tool_results = response.output["tool_results"]
        assert tool_results[0]["output"] == {"echoed": "hi"}
        assert response.metadata["tools_called"] == ["echo"]
        # The continuation is consumed.
        assert not await runtime._approval_store.get(continuation_id)

    @pytest.mark.asyncio
    async def test_denied_continuation_feeds_denial_to_model(self):
        from micro_agent.security import InMemoryApprovalStore

        policy = AgentPolicy(approval_required=True)
        runtime = _echo_runtime(policy=policy, approval_store=InMemoryApprovalStore())
        agent = await runtime.create(_definition())
        paused = await runtime.invoke(agent, AgentRequest(input={}))
        response = await runtime.invoke(
            agent,
            AgentRequest(
                input={},
                continuation_id=paused.metadata["continuation_id"],
                approval_decision="deny",
            ),
        )
        assert response.status == "success"
        tool_results = response.output["tool_results"]
        assert tool_results[0].get("denied") is True
        assert "approval denied" in tool_results[0]["error"]
        assert response.metadata["tools_called"] == []

    @pytest.mark.asyncio
    async def test_unknown_continuation_fails_fast(self):
        runtime = _echo_runtime(policy=AgentPolicy(approval_required=True))
        agent = await runtime.create(_definition())
        with pytest.raises(ContinuationNotFoundError):
            await runtime.invoke(
                agent,
                AgentRequest(input={}, continuation_id="missing", approval_decision="approve"),
            )

    @pytest.mark.asyncio
    async def test_expired_continuation_fails_fast(self):
        from micro_agent.security import InMemoryApprovalStore

        store = InMemoryApprovalStore(default_ttl_seconds=0.0)
        runtime = _echo_runtime(policy=AgentPolicy(approval_required=True), approval_store=store)
        agent = await runtime.create(_definition())
        paused = await runtime.invoke(agent, AgentRequest(input={}))
        with pytest.raises(ContinuationNotFoundError):
            await runtime.invoke(
                agent,
                AgentRequest(
                    input={},
                    continuation_id=paused.metadata["continuation_id"],
                    approval_decision="approve",
                ),
            )

    @pytest.mark.asyncio
    async def test_allowed_tool_executes(self):
        policy = AgentPolicy(allowed_tools=["echo"])
        runtime = _echo_runtime(policy=policy)
        agent = await runtime.create(_definition())
        response = await runtime.invoke(agent, AgentRequest(input={}))
        assert response.metadata["tools_called"] == ["echo"]

    @pytest.mark.asyncio
    async def test_denial_metric_recorded(self):
        from micro_agent.observability import Telemetry

        telemetry = Telemetry()
        policy = AgentPolicy(denied_tools=["echo"])
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                fake_model_config=FakeModelConfig(
                    response="done",
                    tool_requests=[{"name": "echo", "arguments": {"message": "hi"}}],
                ),
                policy=policy,
                telemetry=telemetry,
            )
        )
        agent = await runtime.create(_definition())
        await runtime.invoke(agent, AgentRequest(input={}))
        assert telemetry.metrics.get_metrics("policy_denials_total")

    @pytest.mark.asyncio
    async def test_denied_mcp_fails_startup(self):
        clients = {"residency-services": object()}
        from micro_agent.mcp import McpConnectionManager

        manager = McpConnectionManager(
            client_factory=lambda config: clients[config.ref],
        )
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                policy=AgentPolicy(denied_mcps=["residency-services"]),
                mcp_manager=manager,
            )
        )
        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "m", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "x"},
                    "dependencies": {
                        "model": {"ref": "fake"},
                        "mcp_servers": [
                            {
                                "ref": "residency-services",
                                "endpoint": "https://mcp.example.com",
                            }
                        ],
                    },
                },
            }
        )
        agent = await runtime.create(definition)
        with pytest.raises(PermissionError, match="denied by policy"):
            await runtime.start(agent)

    @pytest.mark.asyncio
    async def test_denied_skill_fails_startup(self):
        policy = AgentPolicy(denied_skills=["submit-renewal"])
        runtime = _echo_runtime(policy=policy)
        definition = _definition(skills=[{"id": "submit-renewal", "name": "Submit Renewal"}])
        agent = await runtime.create(definition)
        with pytest.raises(PermissionError, match="submit-renewal"):
            await runtime.start(agent)

    @pytest.mark.asyncio
    async def test_allowed_skill_passes_startup(self):
        policy = AgentPolicy(allowed_skills=["check-status"])
        runtime = _echo_runtime(policy=policy)
        definition = _definition(skills=[{"id": "check-status", "name": "Check Status"}])
        agent = await runtime.create(definition)
        await runtime.start(agent)

    @pytest.mark.asyncio
    async def test_denied_model_fails_startup(self):
        policy = AgentPolicy(model_restrictions={"denied_models": ["fake-model"]})
        runtime = _echo_runtime(policy=policy)
        agent = await runtime.create(_definition())
        with pytest.raises(PermissionError, match="denied by policy"):
            await runtime.start(agent)

    @pytest.mark.asyncio
    async def test_model_provider_not_in_allow_list_fails_startup(self):
        policy = AgentPolicy(model_restrictions={"allowed_providers": ["anthropic"]})
        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "policy-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test agent."},
                    "dependencies": {
                        "model": {
                            "ref": "fake-model",
                            "provider": "openai-compatible",
                        },
                    },
                },
            }
        )
        runtime = _echo_runtime(policy=policy)
        agent = await runtime.create(definition)
        with pytest.raises(PermissionError, match="openai-compatible"):
            await runtime.start(agent)


class TestOperationRegistry:
    """Idempotency/deduplication applied to tool side effects."""

    @pytest.mark.asyncio
    async def test_duplicate_idempotency_key_deduplicates(self):
        registry = OperationRegistry()
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                fake_model_config=FakeModelConfig(
                    response="done",
                    tool_requests=[
                        {
                            "name": "echo",
                            "arguments": {"idempotency_key": "k-1", "message": "hi"},
                        }
                    ],
                ),
                operation_registry=registry,
            )
        )
        agent = await runtime.create(_definition())
        first = await runtime.invoke(agent, AgentRequest(input={}, request_id="r1"))
        second = await runtime.invoke(agent, AgentRequest(input={}, request_id="r2"))
        assert first.output["tool_results"][0].get("was_deduplicated") is not True
        assert second.output["tool_results"][0]["was_deduplicated"] is True
        await runtime.close()

    @pytest.mark.asyncio
    async def test_redis_registry_deduplicates_across_runtime_replicas(self):
        backend = FakeRedisBackend()
        request = {
            "name": "echo",
            "arguments": {"idempotency_key": "shared-1", "message": "hi"},
        }
        runtime_a = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=OneShotToolProvider(
                    FakeModelConfig(response="done", tool_requests=[request])
                ),
                operation_registry=RedisOperationRegistry(client=FakeRedis(backend)),
            )
        )
        runtime_b = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=OneShotToolProvider(
                    FakeModelConfig(response="done", tool_requests=[request])
                ),
                operation_registry=RedisOperationRegistry(client=FakeRedis(backend)),
            )
        )
        definition = _definition()
        agent_a = await runtime_a.create(definition)
        agent_b = await runtime_b.create(definition)
        tenant_a = UserContext(user_id="alice", tenant_id="tenant-a")
        first = await runtime_a.invoke(
            agent_a, AgentRequest(input={}, request_id="r1", user_context=tenant_a)
        )
        second = await runtime_b.invoke(
            agent_b, AgentRequest(input={}, request_id="r2", user_context=tenant_a)
        )
        assert first.output["tool_results"][0].get("was_deduplicated") is not True
        assert second.output["tool_results"][0]["was_deduplicated"] is True
        assert second.output["tool_results"][0]["output"] == {"echoed": "hi"}
        runtime_c = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=OneShotToolProvider(
                    FakeModelConfig(response="done", tool_requests=[request])
                ),
                operation_registry=RedisOperationRegistry(client=FakeRedis(backend)),
            )
        )
        agent_c = await runtime_c.create(definition)
        third = await runtime_c.invoke(
            agent_c,
            AgentRequest(
                input={},
                request_id="r3",
                user_context=UserContext(user_id="bob", tenant_id="tenant-b"),
            ),
        )
        assert third.output["tool_results"][0].get("was_deduplicated") is not True
        await runtime_a.close()
        await runtime_b.close()
        await runtime_c.close()
