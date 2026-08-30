"""Micro-Agent Identity and Security Context.

Distinguishes agent identity, caller identity, user context,
and runtime/workload identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from micro_agent.core.agent import AgentIdentity

# Re-export for backward compatibility
__all__ = [
    "AgentIdentity",
    "CallerIdentity",
    "RuntimeIdentity",
    "SecurityContext",
    "UserContext",
]


@dataclass(frozen=True)
class CallerIdentity:
    """The identity of the caller invoking the agent."""

    caller_id: str
    caller_type: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserContext:
    """End-user context passed through the invocation chain."""

    user_id: str
    tenant_id: str | None = None
    roles: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeIdentity:
    """The workload/runtime identity (e.g. Kubernetes service account)."""

    workload_id: str
    namespace: str = "default"
    service_account: str | None = None


@dataclass
class SecurityContext:
    """Complete security context for an invocation.

    Agent identity != user identity. No implicit delegation.
    """

    agent_identity: AgentIdentity
    caller_identity: CallerIdentity | None = None
    user_context: UserContext | None = None
    runtime_identity: RuntimeIdentity | None = None
    policy_refs: list[str] = field(default_factory=list)
    credential_refs: list[str] = field(default_factory=list)

    def has_caller(self) -> bool:
        return self.caller_identity is not None

    def has_user_context(self) -> bool:
        return self.user_context is not None
