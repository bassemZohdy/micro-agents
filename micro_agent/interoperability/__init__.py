"""Micro-Agent Interoperability — HTTP API and A2A."""

from micro_agent.interoperability.a2a import (
    A2AConfig,
    A2AMessage,
    A2AResponse,
    A2ATask,
    AgentCard,
    AgentSkill,
    a2a_well_known_path,
    agent_card_from_definition,
    skills_mapping,
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
    "a2a_well_known_path",
    "agent_card_from_definition",
    "create_app",
    "serialize_response",
    "skills_mapping",
]
