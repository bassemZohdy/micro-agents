"""Micro-Agent Cloud control-plane surfaces (C1-C5 reference slices).

This package is the control-plane side of the boundary defined in ADR 0013:
it may import the core framework, but the core framework never imports it,
and nothing here is needed to run a single Micro-Agent.
"""

from cloud.auth import PlaneAuthenticator, install_plane_auth
from cloud.config import (
    ConfigRecord,
    ConfigValidationError,
    EnvironmentSecretResolver,
    InMemoryConfigStore,
    SecretResolver,
    SqliteConfigStore,
    create_config_app,
)
from cloud.config_client import ConfigClient, ConfigPlaneUnreachableError
from cloud.descriptors import (
    DESCRIPTOR_SCHEMA_VERSION,
    AgentDescriptor,
    DescriptorCardMismatchError,
    DescriptorError,
    SkillDescriptor,
    card_fingerprint,
    descriptor_from_definition,
)
from cloud.discovery import (
    DiscoveredAgent,
    RegistryDiscoveryClient,
    RegistryUnreachableError,
)
from cloud.gateway import (
    Caller,
    Gateway,
    GatewayAuthenticationError,
    GatewayAuthenticator,
    GatewayRoute,
    OidcGatewayAuthenticator,
    StaticTokenAuthenticator,
    Target,
    create_gateway_app,
)
from cloud.observability import (
    InMemoryObservabilityStore,
    SqliteObservabilityStore,
    TraceSpan,
    UsageRecord,
    create_observability_app,
)
from cloud.registry import (
    InMemoryAgentRegistry,
    RegistryEntry,
    SqliteAgentRegistry,
    UnknownAgentError,
    create_registry_app,
)
from cloud.schemas import (
    CLOUD_SCHEMA_VERSION,
    AgentDescriptorContract,
    ConfigRecordContract,
    ObservabilityBatchContract,
    ObservabilityEventContract,
    SkillDescriptorContract,
)

__all__ = [
    "Caller",
    "CLOUD_SCHEMA_VERSION",
    "ConfigClient",
    "ConfigPlaneUnreachableError",
    "ConfigRecord",
    "ConfigRecordContract",
    "ConfigValidationError",
    "DESCRIPTOR_SCHEMA_VERSION",
    "AgentDescriptor",
    "AgentDescriptorContract",
    "DescriptorCardMismatchError",
    "DescriptorError",
    "DiscoveredAgent",
    "EnvironmentSecretResolver",
    "Gateway",
    "GatewayAuthenticationError",
    "GatewayAuthenticator",
    "GatewayRoute",
    "OidcGatewayAuthenticator",
    "InMemoryAgentRegistry",
    "InMemoryConfigStore",
    "InMemoryObservabilityStore",
    "ObservabilityBatchContract",
    "ObservabilityEventContract",
    "SqliteAgentRegistry",
    "SqliteConfigStore",
    "SqliteObservabilityStore",
    "RegistryDiscoveryClient",
    "RegistryEntry",
    "RegistryUnreachableError",
    "PlaneAuthenticator",
    "SecretResolver",
    "SkillDescriptor",
    "SkillDescriptorContract",
    "StaticTokenAuthenticator",
    "Target",
    "TraceSpan",
    "UnknownAgentError",
    "UsageRecord",
    "card_fingerprint",
    "create_config_app",
    "create_gateway_app",
    "create_observability_app",
    "create_registry_app",
    "descriptor_from_definition",
    "install_plane_auth",
]
