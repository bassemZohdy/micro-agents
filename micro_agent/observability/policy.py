"""Backward-compatibility shim — moved to :mod:`micro_agent.security.policy`."""

from micro_agent.security.policy import (
    AgentPolicy,
    PolicyDecision,
    PolicyEffect,
    PolicyEvaluator,
    PolicyRule,
)

__all__ = [
    "AgentPolicy",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEvaluator",
    "PolicyRule",
]
