"""Micro-Agent Interoperability — HTTP API and A2A."""

from micro_agent.interoperability.a2a import (
    SUPPORTED_PROTOCOL_VERSIONS,
    A2AConfig,
    A2aSdkUnavailableError,
    UnsupportedProtocolVersionError,
    a2a_well_known_path,
    agent_card_from_definition,
    normalize_protocol_version,
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
    "A2aSdkUnavailableError",
    "CapabilitiesResponse",
    "CapabilitiesResponseModel",
    "HealthResponse",
    "HealthResponseModel",
    "InvokeRequest",
    "InvokeRequestModel",
    "InvokeResponse",
    "InvokeResponseModel",
    "ROUTES",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UnsupportedProtocolVersionError",
    "a2a_well_known_path",
    "agent_card_from_definition",
    "create_app",
    "normalize_protocol_version",
    "serialize_response",
    "skills_mapping",
]
