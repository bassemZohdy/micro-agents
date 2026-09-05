"""Micro-Agent Cloud control-plane surfaces (C1-C4).

This package is the control-plane side of the boundary defined in ADR 0013:
it may import the core framework, but the core framework never imports it,
and nothing here is needed to run a single Micro-Agent.
"""

from cloud.config import (
    ConfigRecord,
    ConfigValidationError,
    EnvironmentSecretResolver,
    InMemoryConfigStore,
    SecretResolver,
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
    StaticTokenAuthenticator,
    Target,
    create_gateway_app,
)
from cloud.observability import (
    InMemoryObservabilityStore,
    TraceSpan,
    UsageRecord,
    create_observability_app,
)
from cloud.registry import (
    InMemoryAgentRegistry,
    RegistryEntry,
    UnknownAgentError,
    create_registry_app,
)

__all__ = [
    "Caller",
    "ConfigClient",
    "ConfigPlaneUnreachableError",
    "ConfigRecord",
    "ConfigValidationError",
    "DESCRIPTOR_SCHEMA_VERSION",
    "AgentDescriptor",
    "DescriptorCardMismatchError",
    "DescriptorError",
    "DiscoveredAgent",
    "EnvironmentSecretResolver",
    "Gateway",
    "GatewayAuthenticationError",
    "GatewayAuthenticator",
    "GatewayRoute",
    "InMemoryAgentRegistry",
    "InMemoryConfigStore",
    "InMemoryObservabilityStore",
    "RegistryDiscoveryClient",
    "RegistryEntry",
    "RegistryUnreachableError",
    "SecretResolver",
    "SkillDescriptor",
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
]
