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
    CapabilitiesResponseModel,
    HealthResponse,
    HealthResponseModel,
    InvokeRequest,
    InvokeRequestModel,
    InvokeResponse,
    InvokeResponseModel,
    create_app,
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
    "CapabilitiesResponseModel",
    "HealthResponse",
    "HealthResponseModel",
    "InvokeRequest",
    "InvokeRequestModel",
    "InvokeResponse",
    "InvokeResponseModel",
    "ROUTES",
    "create_app",
    "serialize_response",
]
