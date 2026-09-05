"""Micro-Agent Security — identity, policy, and safe side effects.

Bounded autonomy: policies are enforced deterministically outside prompt
instructions; prompt injection cannot override platform policy.
"""

from micro_agent.security.approvals import (
    ApprovalStore,
    InMemoryApprovalStore,
    PendingApproval,
    RedisApprovalStore,
)
from micro_agent.security.auth import (
    AuthenticatedIdentity,
    Authenticator,
    OidcJwtAuthenticator,
)
from micro_agent.security.context import build_security_context, resolve_credential
from micro_agent.security.credentials import (
    CredentialProvider,
    EnvironmentCredentialProvider,
    StaticCredentialProvider,
)
from micro_agent.security.identity import (
    AgentIdentity,
    CallerIdentity,
    RuntimeIdentity,
    SecurityContext,
    UserContext,
)
from micro_agent.security.policy import (
    AgentPolicy,
    PolicyDecision,
    PolicyEffect,
    PolicyEvaluator,
    PolicyRule,
)
from micro_agent.security.propagation import (
    InvocationIdentity,
    get_invocation_identity,
    invocation_identity,
    reset_invocation_identity,
    resolve_workload_identity,
    set_invocation_identity,
)
from micro_agent.security.redis_operations import RedisOperationRegistry
from micro_agent.security.side_effects import (
    Operation,
    OperationRegistry,
    OperationRegistryProtocol,
    OperationResult,
    RetryClassification,
)

__all__ = [
    "AuthenticatedIdentity",
    "ApprovalStore",
    "Authenticator",
    "AgentIdentity",
    "AgentPolicy",
    "CallerIdentity",
    "CredentialProvider",
    "EnvironmentCredentialProvider",
    "InMemoryApprovalStore",
    "OidcJwtAuthenticator",
    "InvocationIdentity",
    "Operation",
    "OperationRegistry",
    "OperationRegistryProtocol",
    "OperationResult",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEvaluator",
    "PendingApproval",
    "PolicyRule",
    "RedisApprovalStore",
    "RetryClassification",
    "RedisOperationRegistry",
    "RuntimeIdentity",
    "SecurityContext",
    "StaticCredentialProvider",
    "UserContext",
    "build_security_context",
    "get_invocation_identity",
    "invocation_identity",
    "reset_invocation_identity",
    "resolve_credential",
    "resolve_workload_identity",
    "set_invocation_identity",
]
