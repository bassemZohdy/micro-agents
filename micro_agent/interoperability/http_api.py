"""Micro-Agent HTTP API.

Exposes a Micro-Agent as an independent network service.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# API Models
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
    """Serialize a response to JSON."""
    if hasattr(data, "__dict__"):
        return json.dumps(data.__dict__, default=str)
    return json.dumps(data, default=str)
