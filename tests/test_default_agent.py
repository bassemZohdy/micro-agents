"""Tests for DefaultMicroAgent and HTTP server."""

import asyncio
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from micro_agent.core import (
    AgentCapabilities,
    AgentRequest,
    AgentResponse,
    AgentState,
    DefaultMicroAgent,
)
from micro_agent.definition import (
    ConcurrencyPolicy,
    ContractValidationError,
    InputContract,
    load_definition_from_dict,
)
from micro_agent.interoperability import create_app, serialize_response
from micro_agent.observability import HealthChecker, HealthStatus
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from runtimes.adk import AdkRuntime


class ControlledRuntime(AgentRuntime):
    """Runtime test double that can hold concurrent invocations in flight."""

    def __init__(self, expected_invocations: int = 1, failures: int = 0) -> None:
        self.expected_invocations = expected_invocations
        self.failures = failures
        self.entered = 0
        self.all_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.stop_called = False

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities()

    async def create(self, definition) -> RuntimeAgent:
        return RuntimeAgent(
            identity=definition_identity(definition),
            capabilities=AgentCapabilities(),
        )

    async def start(self, agent: RuntimeAgent) -> None:
        return None

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        self.entered += 1
        if self.entered >= self.expected_invocations:
            self.all_entered.set()
        await self.release.wait()
        if self.failures:
            self.failures -= 1
            raise RuntimeError("controlled invocation failure")
        return AgentResponse(output={"ok": True}, request_id=request.request_id)

    async def stop(self, agent: RuntimeAgent) -> None:
        self.stop_called = True

    async def shutdown(self, agent: RuntimeAgent) -> None:
        return None


class TimeoutRuntime(ControlledRuntime):
    """Runtime double that surfaces an exhausted invocation deadline."""

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        raise TimeoutError("invocation deadline exceeded")


def definition_identity(definition):
    from micro_agent.core import AgentIdentity

    return AgentIdentity(
        agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
        agent_name=definition.metadata.name,
        agent_version=definition.metadata.version,
    )


@pytest.fixture
def definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "You are a test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "skills": [{"id": "check", "name": "Check", "description": "Check things"}],
                },
            },
        }
    )


@pytest.fixture
def agent(definition):
    runtime = AdkRuntime()
    return DefaultMicroAgent(definition, runtime)


class TestDefaultMicroAgent:
    """Test concrete MicroAgent lifecycle."""

    @pytest.mark.asyncio
    async def test_lifecycle(self, agent):
        assert agent.state == AgentState.CREATED
        await agent.initialize()
        assert agent.state == AgentState.INITIALIZED
        await agent.start()
        assert agent.state == AgentState.READY
        response = await agent.invoke(AgentRequest(input={"action": "test"}))
        assert response.status == "success"
        assert agent.state == AgentState.READY
        await agent.stop()
        assert agent.state == AgentState.STOPPED
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_identity(self, agent):
        assert agent.identity.agent_name == "test-agent"
        assert agent.identity.agent_version == "1.0.0"

    @pytest.mark.asyncio
    async def test_cannot_invoke_before_start(self, agent):
        await agent.initialize()
        with pytest.raises(RuntimeError, match="Cannot invoke"):
            await agent.invoke(AgentRequest())

    @pytest.mark.asyncio
    async def test_cannot_start_twice(self, agent):
        await agent.initialize()
        await agent.start()
        await agent.stop()

    @pytest.mark.asyncio
    async def test_capabilities(self, agent):
        caps = agent.capabilities
        assert caps.streaming is False

    @pytest.mark.asyncio
    async def test_concurrent_invocations_do_not_change_lifecycle_state(self, definition):
        runtime = ControlledRuntime(expected_invocations=2)
        agent = DefaultMicroAgent(definition, runtime)
        await agent.initialize()
        await agent.start()

        first = asyncio.create_task(agent.invoke(AgentRequest()))
        second = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        assert agent.state == AgentState.READY

        runtime.release.set()
        responses = await asyncio.gather(first, second)
        assert all(response.status == "success" for response in responses)
        assert agent.state == AgentState.READY

    @pytest.mark.asyncio
    async def test_invocation_failure_does_not_poison_agent(self, definition):
        runtime = ControlledRuntime(failures=1)
        runtime.release.set()
        agent = DefaultMicroAgent(definition, runtime)
        await agent.initialize()
        await agent.start()

        with pytest.raises(RuntimeError, match="controlled invocation failure"):
            await agent.invoke(AgentRequest())
        assert agent.state == AgentState.READY
        assert (await agent.invoke(AgentRequest())).status == "success"

    @pytest.mark.asyncio
    async def test_stop_drains_in_flight_invocations(self, definition):
        runtime = ControlledRuntime()
        agent = DefaultMicroAgent(definition, runtime)
        await agent.initialize()
        await agent.start()

        invocation = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        stopping = asyncio.create_task(agent.stop())
        await asyncio.sleep(0)
        assert agent.state == AgentState.STOPPING
        assert runtime.stop_called is False

        runtime.release.set()
        await invocation
        await stopping
        assert runtime.stop_called is True
        assert agent.state == AgentState.STOPPED

    @pytest.mark.asyncio
    async def test_reject_policy_enforces_concurrency_limit(self, definition):
        limited = definition.model_copy(deep=True)
        limited.spec.runtime.max_concurrency = 1
        limited.spec.runtime.concurrency_policy = ConcurrencyPolicy.REJECT
        runtime = ControlledRuntime(expected_invocations=1)
        agent = DefaultMicroAgent(limited, runtime)
        await agent.initialize()
        await agent.start()

        first = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        with pytest.raises(RuntimeError, match="concurrency limit"):
            await agent.invoke(AgentRequest())

        runtime.release.set()
        await first
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_wait_policy_releases_capacity_after_completion(self, definition):
        limited = definition.model_copy(deep=True)
        limited.spec.runtime.max_concurrency = 1
        runtime = ControlledRuntime(expected_invocations=1)
        agent = DefaultMicroAgent(limited, runtime)
        await agent.initialize()
        await agent.start()

        first = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        second = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.sleep(0)
        assert runtime.entered == 1

        runtime.release.set()
        await asyncio.gather(first, second)
        assert runtime.entered == 2
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_cancelled_invocation_releases_capacity(self, definition):
        limited = definition.model_copy(deep=True)
        limited.spec.runtime.max_concurrency = 1
        runtime = ControlledRuntime(expected_invocations=1)
        agent = DefaultMicroAgent(limited, runtime)
        await agent.initialize()
        await agent.start()

        invocation = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await invocation
        assert agent._active_invocations == 0

        runtime.all_entered.clear()
        follow_up = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        assert runtime.entered == 2
        runtime.release.set()
        await follow_up
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_deadline_cancels_stuck_invocation(self, definition):
        limited = definition.model_copy(deep=True)
        limited.spec.runtime.shutdown_timeout_seconds = 0.05
        runtime = ControlledRuntime(expected_invocations=1)
        agent = DefaultMicroAgent(limited, runtime)
        await agent.initialize()
        await agent.start()

        invocation = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        await agent.stop()

        assert runtime.stop_called is True
        assert agent.state == AgentState.STOPPED
        with pytest.raises(asyncio.CancelledError):
            await invocation

    @pytest.mark.asyncio
    async def test_repeated_stop_waits_for_the_existing_shutdown(self, definition):
        runtime = ControlledRuntime()
        agent = DefaultMicroAgent(definition, runtime)
        await agent.initialize()
        await agent.start()

        invocation = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        first_stop = asyncio.create_task(agent.stop())
        second_stop = asyncio.create_task(agent.stop())
        await asyncio.sleep(0)
        assert agent.state == AgentState.STOPPING
        runtime.release.set()
        await asyncio.gather(invocation, first_stop, second_stop)
        assert agent.state == AgentState.STOPPED
        assert runtime.stop_called is True

    @pytest.mark.asyncio
    async def test_input_contract_is_enforced_before_runtime(self, definition):
        limited = definition.model_copy(deep=True)
        limited.spec.behavior.input_contract = InputContract.model_validate(
            {"parameters": [{"name": "action", "type": "string"}]}
        )
        runtime = ControlledRuntime()
        agent = DefaultMicroAgent(limited, runtime)
        await agent.initialize()
        await agent.start()

        with pytest.raises(ContractValidationError, match="missing required"):
            await agent.invoke(AgentRequest(input={}))
        assert runtime.entered == 0
        assert agent.state == AgentState.READY

    @pytest.mark.asyncio
    async def test_cancelled_waiter_releases_capacity(self, definition):
        limited = definition.model_copy(deep=True)
        limited.spec.runtime.max_concurrency = 1
        runtime = ControlledRuntime(expected_invocations=1)
        agent = DefaultMicroAgent(limited, runtime)
        await agent.initialize()
        await agent.start()

        first = asyncio.create_task(agent.invoke(AgentRequest()))
        await asyncio.wait_for(runtime.all_entered.wait(), timeout=1)
        waiter = asyncio.create_task(agent.invoke(AgentRequest()))
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        runtime.release.set()
        await first
        await agent.stop()
        await agent.shutdown()


class TestHTTPServer:
    """Test FastAPI HTTP server."""

    @pytest.mark.asyncio
    async def test_invoke_endpoint(self, agent):
        await agent.initialize()
        await agent.start()
        app = create_app(agent)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/invoke",
                json={"input": {"action": "test"}},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert UUID(data["request_id"])
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_contract_error_returns_stable_422(self, agent):
        agent.definition.spec.behavior.input_contract = InputContract.model_validate(
            {"parameters": [{"name": "action", "type": "string"}]}
        )
        await agent.initialize()
        await agent.start()
        app = create_app(agent)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/invoke", json={"input": {}})
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "contract_validation_failed"
            assert resp.json()["detail"]["contract"] == "input"
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_overload_returns_stable_429(self, agent):
        await agent.initialize()
        await agent.start()
        agent._definition.spec.runtime.max_concurrency = 1
        agent._definition.spec.runtime.concurrency_policy = "reject"
        # The first slot is occupied from the service's perspective; this
        # isolates the HTTP mapping without racing a second client task.
        agent._active_invocations = 1
        app = create_app(agent)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/invoke", json={"input": {}})
            assert resp.status_code == 429
            assert resp.headers["retry-after"] == "1"
            assert resp.json()["detail"] == {"code": "invocation_overloaded", "limit": 1}
        agent._active_invocations = 0
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_request_size_limit_returns_413(self, agent):
        await agent.initialize()
        await agent.start()
        app = create_app(agent, max_request_bytes=8)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/invoke", json={"input": {}})
            assert resp.status_code == 413
            assert resp.json()["code"] == "request_too_large"
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_deadline_error_returns_stable_504(self, definition):
        agent = DefaultMicroAgent(definition, TimeoutRuntime())
        await agent.initialize()
        await agent.start()
        app = create_app(agent)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/invoke",
                json={"input": {}, "timeout_seconds": 0.1},
            )
            assert resp.status_code == 504
            assert resp.json()["detail"] == {
                "code": "deadline_exceeded",
                "message": "Invocation deadline exceeded",
            }
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_health_live(self, agent):
        await agent.initialize()
        await agent.start()
        app = create_app(agent)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/live")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_health_ready(self, agent):
        await agent.initialize()
        await agent.start()
        checker = HealthChecker()
        checker.add_dependency("model")
        app = create_app(agent, checker)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_health_not_ready_returns_503(self, agent):
        await agent.initialize()
        await agent.start()
        checker = HealthChecker()
        checker.add_dependency("model", status=HealthStatus.UNHEALTHY)
        app = create_app(agent, checker)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health/ready")
            assert resp.status_code == 503
            assert resp.json()["details"]["ready"] is False
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_capabilities_endpoint(self, agent):
        await agent.initialize()
        await agent.start()
        app = create_app(agent)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/capabilities")
            assert resp.status_code == 200
            data = resp.json()
            assert data["agent_name"] == "test-agent"
            assert len(data["skills"]) == 1
        await agent.stop()
        await agent.shutdown()


class TestSerializeResponse:
    """Test fixed serialize_response."""

    def test_serializes_nested_dataclasses(self):
        from micro_agent.observability import DependencyHealth, HealthStatus, ReadinessResult

        result = ReadinessResult(
            ready=True,
            status=HealthStatus.HEALTHY,
            dependencies=[DependencyHealth(name="model", status=HealthStatus.HEALTHY)],
        )
        serialized = serialize_response(result)
        import json

        parsed = json.loads(serialized)
        assert parsed["ready"] is True
        assert parsed["dependencies"][0]["name"] == "model"
        assert parsed["dependencies"][0]["status"] == "healthy"
