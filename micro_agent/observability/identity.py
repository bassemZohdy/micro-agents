"""Backward-compatibility shim — moved to :mod:`micro_agent.security.identity`."""

from micro_agent.core.agent import AgentIdentity
from micro_agent.security.identity import (
    CallerIdentity,
    RuntimeIdentity,
    SecurityContext,
    UserContext,
)

__all__ = [
    "AgentIdentity",
    "CallerIdentity",
    "RuntimeIdentity",
    "SecurityContext",
    "UserContext",
]
