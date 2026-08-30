"""Micro-Agent Bounded Autonomy and Policy.

Policies are enforced outside prompt instructions where possible.
Prompt injection cannot simply override deterministic platform policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Policy Model
# ---------------------------------------------------------------------------


class PolicyEffect(StrEnum):
    """Policy effect."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass
class PolicyRule:
    """A single policy rule."""

    effect: PolicyEffect
    resource: str
    actions: list[str] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentPolicy:
    """Policy governing agent autonomy boundaries."""

    allowed_skills: list[str] = field(default_factory=list)
    denied_skills: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    allowed_mcps: list[str] = field(default_factory=list)
    denied_mcps: list[str] = field(default_factory=list)
    model_restrictions: dict[str, Any] = field(default_factory=dict)
    side_effect_policy: str = "allow"
    approval_required: bool = False
    rules: list[PolicyRule] = field(default_factory=list)

    def is_skill_allowed(self, skill_id: str) -> bool:
        """Check if a skill is allowed by policy."""
        if skill_id in self.denied_skills:
            return False
        return not (self.allowed_skills and skill_id not in self.allowed_skills)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed by policy."""
        if tool_name in self.denied_tools:
            return False
        return not (self.allowed_tools and tool_name not in self.allowed_tools)

    def is_mcp_allowed(self, mcp_ref: str) -> bool:
        """Check if an MCP server is allowed by policy."""
        if mcp_ref in self.denied_mcps:
            return False
        return not (self.allowed_mcps and mcp_ref not in self.allowed_mcps)


# ---------------------------------------------------------------------------
# Policy Evaluator
# ---------------------------------------------------------------------------


@dataclass
class PolicyDecision:
    """Result of a policy evaluation."""

    allowed: bool
    reason: str = ""
    rule: PolicyRule | None = None


class PolicyEvaluator:
    """Evaluates policies against requested actions."""

    def __init__(self, policy: AgentPolicy) -> None:
        self._policy = policy

    def evaluate_skill(self, skill_id: str) -> PolicyDecision:
        """Evaluate whether a skill invocation is allowed."""
        if not self._policy.is_skill_allowed(skill_id):
            return PolicyDecision(
                allowed=False,
                reason=f"Skill '{skill_id}' is denied by policy",
            )
        return PolicyDecision(allowed=True)

    def evaluate_tool(self, tool_name: str) -> PolicyDecision:
        """Evaluate whether a tool invocation is allowed."""
        if not self._policy.is_tool_allowed(tool_name):
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is denied by policy",
            )
        return PolicyDecision(allowed=True)

    def evaluate_mcp(self, mcp_ref: str) -> PolicyDecision:
        """Evaluate whether an MCP server connection is allowed."""
        if not self._policy.is_mcp_allowed(mcp_ref):
            return PolicyDecision(
                allowed=False,
                reason=f"MCP '{mcp_ref}' is denied by policy",
            )
        return PolicyDecision(allowed=True)

    def evaluate_side_effect(self, operation: str) -> PolicyDecision:
        """Evaluate whether a side-effect operation is allowed."""
        if self._policy.side_effect_policy == "deny":
            return PolicyDecision(
                allowed=False,
                reason=f"Side-effect '{operation}' denied by policy",
            )
        if self._policy.approval_required:
            return PolicyDecision(
                allowed=False,
                reason=f"Side-effect '{operation}' requires approval",
            )
        return PolicyDecision(allowed=True)
