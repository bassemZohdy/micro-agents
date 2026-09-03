"""Micro-Agent Cloud (C1) — descriptor, registry, and discovery surfaces.

This package is the control-plane side of the boundary defined in ADR 0013:
it may import the core framework, but the core framework never imports it,
and nothing here is needed to run a single Micro-Agent.
"""

from cloud.descriptors import (
    DESCRIPTOR_SCHEMA_VERSION,
    AgentDescriptor,
    DescriptorCardMismatchError,
    DescriptorError,
    SkillDescriptor,
    descriptor_from_definition,
)
from cloud.discovery import (
    DiscoveredAgent,
    RegistryDiscoveryClient,
    RegistryUnreachableError,
)
from cloud.registry import (
    InMemoryAgentRegistry,
    RegistryEntry,
    UnknownAgentError,
    create_registry_app,
)

__all__ = [
    "DESCRIPTOR_SCHEMA_VERSION",
    "AgentDescriptor",
    "DescriptorCardMismatchError",
    "DescriptorError",
    "DiscoveredAgent",
    "InMemoryAgentRegistry",
    "RegistryDiscoveryClient",
    "RegistryEntry",
    "RegistryUnreachableError",
    "SkillDescriptor",
    "UnknownAgentError",
    "create_registry_app",
    "descriptor_from_definition",
]
