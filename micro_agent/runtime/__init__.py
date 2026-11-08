"""Micro-Agent Runtime — SPI for runtime implementations."""

from micro_agent.runtime.guarantees import (
    UNIFORM_GUARANTEES,
    Guarantee,
    GuaranteeStatus,
    guarantees_by_status,
)
from micro_agent.runtime.registry import (
    RUNTIME_ENTRY_POINT_GROUP,
    RuntimeBuildContext,
    RuntimeFactory,
    RuntimeRegistration,
    RuntimeRegistry,
    UnknownRuntimeError,
    default_registry,
    get_runtime_registration,
    register_runtime,
    registered_runtimes,
)
from micro_agent.runtime.runtime import (
    AgentRuntime,
    RuntimeAgent,
    RuntimeCapabilities,
)

__all__ = [
    "RUNTIME_ENTRY_POINT_GROUP",
    "UNIFORM_GUARANTEES",
    "AgentRuntime",
    "Guarantee",
    "GuaranteeStatus",
    "RuntimeAgent",
    "RuntimeBuildContext",
    "RuntimeCapabilities",
    "RuntimeFactory",
    "RuntimeRegistration",
    "RuntimeRegistry",
    "UnknownRuntimeError",
    "default_registry",
    "get_runtime_registration",
    "guarantees_by_status",
    "register_runtime",
    "registered_runtimes",
]
