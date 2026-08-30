"""Micro-Agent Interoperability — HTTP API and A2A."""

from micro_agent.interoperability.a2a import (
    A2AConfig,
    A2AMessage,
    A2AResponse,
    A2ATask,
    AgentCard,
    AgentSkill,
)
from micro_agent.interoperability.http_api import (
    ROUTES,
    CapabilitiesResponse,
    HealthResponse,
    InvokeRequest,
    InvokeResponse,
    serialize_response,
)

__all__ = [
    "A2AConfig",
    "A2AMessage",
    "A2AResponse",
    "A2ATask",
    "AgentCard",
    "AgentSkill",
    "CapabilitiesResponse",
    "HealthResponse",
    "InvokeRequest",
    "InvokeResponse",
    "ROUTES",
    "serialize_response",
]
