"""Tests for Micro-Agent HTTP API."""

import json

from micro_agent.interoperability import (
    ROUTES,
    CapabilitiesResponse,
    HealthResponse,
    InvokeRequest,
    InvokeResponse,
    serialize_response,
)


class TestInvokeRequest:
    """Test invocation request."""

    def test_default_request(self):
        req = InvokeRequest()
        assert req.input == {}
        assert req.request_id is None

    def test_request_with_data(self):
        req = InvokeRequest(
            input={"action": "check"},
            request_id="req-1",
            session_id="sess-1",
        )
        assert req.input["action"] == "check"


class TestInvokeResponse:
    """Test invocation response."""

    def test_success_response(self):
        resp = InvokeResponse(output={"result": "ok"})
        assert resp.status == "success"
        assert resp.error is None

    def test_error_response(self):
        resp = InvokeResponse(status="error", error="something failed")
        assert resp.status == "error"


class TestHealthResponse:
    """Test health response."""

    def test_healthy(self):
        resp = HealthResponse()
        assert resp.status == "healthy"

    def test_with_details(self):
        resp = HealthResponse(
            status="degraded",
            details={"model": "unavailable"},
        )
        assert resp.status == "degraded"


class TestCapabilitiesResponse:
    """Test capabilities response."""

    def test_default(self):
        resp = CapabilitiesResponse()
        assert resp.agent_name == ""
        assert resp.skills == []

    def test_with_data(self):
        resp = CapabilitiesResponse(
            agent_name="test-agent",
            agent_version="1.0.0",
            skills=[{"id": "check", "name": "Check"}],
        )
        assert resp.agent_name == "test-agent"
        assert len(resp.skills) == 1


class TestRoutes:
    """Test route definitions."""

    def test_routes_exist(self):
        assert "POST /v1/invoke" in ROUTES
        assert "GET /metrics" in ROUTES
        assert "GET /health/live" in ROUTES
        assert "GET /health/ready" in ROUTES
        assert "GET /v1/capabilities" in ROUTES


class TestSerializeResponse:
    """Test response serialization."""

    def test_serialize_dataclass(self):
        resp = HealthResponse(status="healthy")
        result = serialize_response(resp)
        parsed = json.loads(result)
        assert parsed["status"] == "healthy"

    def test_serialize_dict(self):
        data = {"key": "value"}
        result = serialize_response(data)
        assert json.loads(result) == data
