"""Micro-Agent Security — identity, policy, and safe side effects.

Bounded autonomy: policies are enforced deterministically outside prompt
instructions; prompt injection cannot override platform policy.
"""

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
from micro_agent.security.side_effects import (
    Operation,
    OperationRegistry,
    OperationResult,
    RetryClassification,
)

__all__ = [
    "AgentIdentity",
    "AgentPolicy",
    "CallerIdentity",
    "CredentialProvider",
    "EnvironmentCredentialProvider",
    "Operation",
    "OperationRegistry",
    "OperationResult",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEvaluator",
    "PolicyRule",
    "RetryClassification",
    "RuntimeIdentity",
    "SecurityContext",
    "StaticCredentialProvider",
    "UserContext",
    "build_security_context",
    "resolve_credential",
]
