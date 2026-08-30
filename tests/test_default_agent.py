"""Tests for DefaultMicroAgent and HTTP server."""

import pytest
from httpx import ASGITransport, AsyncClient

from micro_agent.core import AgentRequest, AgentState, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app, serialize_response
from micro_agent.observability import HealthChecker
from runtimes.adk import AdkRuntime


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
