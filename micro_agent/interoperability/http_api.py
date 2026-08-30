"""Micro-Agent HTTP API — FastAPI application.

Exposes a Micro-Agent as an independent network service.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.interoperability.a2a import (
    a2a_well_known_path,
    agent_card_from_definition,
)
from micro_agent.observability import HealthChecker, Telemetry

# ---------------------------------------------------------------------------
# API Models (Pydantic for FastAPI)
# ---------------------------------------------------------------------------


class InvokeRequestModel(BaseModel):
    """HTTP invocation request."""

    input: dict[str, Any] = {}
    request_id: str | None = None
    session_id: str | None = None
    caller_metadata: dict[str, Any] = {}


class InvokeResponseModel(BaseModel):
    """HTTP invocation response."""

    output: dict[str, Any] = {}
    request_id: str = ""
    session_id: str | None = None
    status: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = {}


class HealthResponseModel(BaseModel):
    """Health check response."""

    status: str = "healthy"
    details: dict[str, Any] = {}


class CapabilitiesResponseModel(BaseModel):
    """Capabilities response."""

    agent_name: str = ""
    agent_version: str = ""
    skills: list[dict[str, Any]] = []
    capabilities: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Legacy dataclass models (kept for backward compat)
# ---------------------------------------------------------------------------


@dataclass
class InvokeRequest:
    """HTTP invocation request."""

    input: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    session_id: str | None = None
    caller_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InvokeResponse:
    """HTTP invocation response."""

    output: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: str | None = None
    status: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthResponse:
    """Health check response."""

    status: str = "healthy"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilitiesResponse:
    """Capabilities response."""

    agent_name: str = ""
    agent_version: str = ""
    skills: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Route Definitions
# ---------------------------------------------------------------------------


ROUTES = {
    "POST /v1/invoke": "Invoke the agent",
    "GET /health/live": "Liveness check",
    "GET /health/ready": "Readiness check",
    "GET /v1/capabilities": "Agent capabilities",
}


def serialize_response(data: Any) -> str:
    """Serialize a response to JSON, handling nested dataclasses."""
    if hasattr(data, "__dataclass_fields__"):
        return json.dumps(asdict(data), default=str)
    if hasattr(data, "model_dump"):
        return json.dumps(data.model_dump(), default=str)
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# FastAPI Application Factory
# ---------------------------------------------------------------------------


def create_app(
    agent: DefaultMicroAgent,
    health_checker: HealthChecker | None = None,
    telemetry: Telemetry | None = None,
    base_url: str | None = None,
) -> FastAPI:
    """Create a FastAPI application for a Micro-Agent."""
    app = FastAPI(
        title=agent.definition.metadata.name,
        version=agent.definition.metadata.version,
        description=agent.definition.metadata.description or "",
    )
    checker = health_checker or HealthChecker()
    telemetry = telemetry or Telemetry()
    telemetry.logger.set_context(
        agent_id=agent.identity.agent_id,
        agent_version=agent.identity.agent_version,
    )

    agent_card = agent_card_from_definition(agent.definition, base_url=base_url)

    @app.get(a2a_well_known_path(), response_model=None)
    async def get_agent_card() -> dict[str, Any]:
        """A2A well-known agent card for discovery."""
        telemetry.increment("http_requests_total", {"route": "/.well-known/agent.json"})
        return asdict(agent_card)

    @app.post("/v1/invoke", response_model=InvokeResponseModel)
    async def invoke(request: InvokeRequestModel) -> InvokeResponseModel:
        telemetry.increment("http_requests_total", {"route": "/v1/invoke", "method": "POST"})
        agent_request = AgentRequest(
            input=request.input,
            request_id=request.request_id or "",
            session_id=request.session_id,
            caller_metadata=request.caller_metadata,
        )
        telemetry.logger.info(
            "invoke request",
            request_id=agent_request.request_id,
            session_id=request.session_id,
        )
        response = await agent.invoke(agent_request)
        telemetry.logger.info(
            "invoke completed",
            request_id=agent_request.request_id,
            status=response.status,
        )
        return InvokeResponseModel(
            output=response.output,
            request_id=response.request_id,
            session_id=response.session_id,
            status=response.status,
            error=response.error,
            metadata=response.metadata,
        )

    @app.get("/health/live", response_model=HealthResponseModel)
    async def liveness() -> HealthResponseModel:
        result = checker.check_liveness()
        return HealthResponseModel(
            status="healthy" if result.alive else "unhealthy",
            details=result.details,
        )

    @app.get("/health/ready", response_model=HealthResponseModel)
    async def readiness() -> HealthResponseModel:
        result = await checker.probe_readiness()
        return HealthResponseModel(
            status=result.status.value,
            details={
                "ready": result.is_ready,
                "dependencies": [
                    {"name": d.name, "status": d.status.value} for d in result.dependencies
                ],
            },
        )

    @app.get("/v1/capabilities", response_model=CapabilitiesResponseModel)
    async def capabilities() -> CapabilitiesResponseModel:
        caps = agent.capabilities
        skills = [
            {"id": s.id, "name": s.name, "description": s.description}
            for s in agent.definition.spec.dependencies.skills
        ]
        return CapabilitiesResponseModel(
            agent_name=agent.identity.agent_name,
            agent_version=agent.identity.agent_version,
            skills=skills,
            capabilities={
                "streaming": caps.streaming,
                "structured_output": caps.structured_output,
                "memory": caps.memory,
                "mcp": caps.mcp,
                "a2a": caps.a2a,
            },
        )

    return app
