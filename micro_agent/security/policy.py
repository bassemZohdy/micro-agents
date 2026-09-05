"""Micro-Agent Bounded Autonomy and Policy.

Policies are enforced outside prompt instructions where possible.
Prompt injection cannot simply override deterministic platform policy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fnmatch import fnmatchcase
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
    requires_approval: bool = False


class PolicyEvaluator:
    """Evaluates policies against requested actions."""

    def __init__(self, policy: AgentPolicy) -> None:
        self._policy = policy

    def evaluate_skill(
        self, skill_id: str, *, context: Mapping[str, Any] | None = None
    ) -> PolicyDecision:
        """Evaluate whether a skill invocation is allowed."""
        if not self._policy.is_skill_allowed(skill_id):
            return PolicyDecision(
                allowed=False,
                reason=f"Skill '{skill_id}' is denied by policy",
            )
        rule_decision = self._rule_decision(f"skill:{skill_id}", "invoke", context)
        if rule_decision is not None:
            return rule_decision
        return PolicyDecision(allowed=True)

    def evaluate_tool(
        self, tool_name: str, *, context: Mapping[str, Any] | None = None
    ) -> PolicyDecision:
        """Evaluate whether a tool invocation is allowed."""
        if not self._policy.is_tool_allowed(tool_name):
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is denied by policy",
            )
        rule_decision = self._rule_decision(f"tool:{tool_name}", "invoke", context)
        if rule_decision is not None:
            return rule_decision
        return PolicyDecision(allowed=True)

    def evaluate_mcp(
        self, mcp_ref: str, *, context: Mapping[str, Any] | None = None
    ) -> PolicyDecision:
        """Evaluate whether an MCP server connection is allowed."""
        if not self._policy.is_mcp_allowed(mcp_ref):
            return PolicyDecision(
                allowed=False,
                reason=f"MCP '{mcp_ref}' is denied by policy",
            )
        rule_decision = self._rule_decision(f"mcp:{mcp_ref}", "connect", context)
        if rule_decision is not None:
            return rule_decision
        return PolicyDecision(allowed=True)

    def evaluate_model(
        self,
        model_ref: str,
        model_id: str | None = None,
        provider: str | None = None,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> PolicyDecision:
        """Evaluate model restrictions for a declared model.

        ``model_restrictions`` supports ``allowed_models``/``denied_models``
        (matched against the model reference and the provider model ID) and
        ``allowed_providers``/``denied_providers`` (matched against the
        provider name). An empty restrictions mapping allows everything.
        """
        restrictions = self._policy.model_restrictions
        candidates = {value for value in (model_ref, model_id) if value}

        denied_models = set(restrictions.get("denied_models") or [])
        if candidates & denied_models:
            return PolicyDecision(
                allowed=False,
                reason="Model is denied by policy",
            )
        allowed_models = set(restrictions.get("allowed_models") or [])
        if allowed_models and not candidates & allowed_models:
            return PolicyDecision(
                allowed=False,
                reason="Model is not in the policy-allowed model set",
            )

        if provider:
            if provider in set(restrictions.get("denied_providers") or []):
                return PolicyDecision(
                    allowed=False,
                    reason=f"Model provider '{provider}' is denied by policy",
                )
            allowed_providers = set(restrictions.get("allowed_providers") or [])
            if allowed_providers and provider not in allowed_providers:
                return PolicyDecision(
                    allowed=False,
                    reason=f"Model provider '{provider}' is not in the policy-allowed set",
                )
        rule_decision = self._rule_decision(f"model:{model_ref}", "invoke", context)
        if rule_decision is not None:
            return rule_decision
        return PolicyDecision(allowed=True)

    def evaluate_side_effect(
        self, operation: str, *, context: Mapping[str, Any] | None = None
    ) -> PolicyDecision:
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
                requires_approval=True,
            )
        rule_decision = self._rule_decision(f"side_effect:{operation}", "execute", context)
        if rule_decision is not None:
            return rule_decision
        return PolicyDecision(allowed=True)

    def _rule_decision(
        self,
        resource: str,
        action: str,
        context: Mapping[str, Any] | None,
    ) -> PolicyDecision | None:
        """Apply matching declarative rules after the legacy policy checks.

        Rules are additive to the explicit allow/deny lists. A matching deny
        always wins; a matching allow is returned when no matching deny exists.
        Conditions use verified invocation identity fields when an invocation
        is active, so request metadata cannot spoof a policy context.
        """
        matching = [
            rule
            for rule in self._policy.rules
            if fnmatchcase(resource, rule.resource)
            and (not rule.actions or action in rule.actions or "*" in rule.actions)
            and _conditions_match(rule.conditions, _policy_context(context))
        ]
        for rule in matching:
            if rule.effect == PolicyEffect.DENY:
                return PolicyDecision(
                    allowed=False,
                    reason=f"{resource} is denied by policy rule",
                    rule=rule,
                )
        for rule in matching:
            if rule.effect == PolicyEffect.ALLOW:
                return PolicyDecision(
                    allowed=True,
                    reason=f"{resource} is allowed by policy rule",
                    rule=rule,
                )
        return None


def _policy_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge caller context with verified identity from the active invocation."""
    values = dict(context or {})
    try:
        from micro_agent.security.propagation import get_invocation_identity

        identity = get_invocation_identity()
    except ImportError:  # pragma: no cover - defensive for partial installations
        identity = None
    if identity is None:
        return values

    caller = identity.caller
    user = identity.user
    workload = identity.workload
    if caller is not None:
        values["caller"] = {
            "id": caller.caller_id,
            "type": caller.caller_type,
        }
        values["caller_id"] = caller.caller_id
        values["caller_type"] = caller.caller_type
    if user is not None:
        values["user"] = {
            "id": user.user_id,
            "tenant_id": user.tenant_id,
            "roles": list(user.roles),
        }
        values["user_id"] = user.user_id
        values["tenant_id"] = user.tenant_id
        values["roles"] = list(user.roles)
    if workload is not None:
        values["workload"] = {
            "id": workload.workload_id,
            "namespace": workload.namespace,
            "service_account": workload.service_account,
        }
        values["workload_id"] = workload.workload_id
        values["workload_namespace"] = workload.namespace
        values["service_account"] = workload.service_account
    return values


_MISSING = object()
_CONDITION_OPERATORS = {
    "eq",
    "equals",
    "not_equals",
    "in",
    "not_in",
    "contains",
    "contains_any",
    "contains_all",
    "matches",
    "exists",
}


def _conditions_match(conditions: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """Return true only when every declared condition matches the context."""
    for key, expected in conditions.items():
        actual = _lookup_context(context, key)
        if not _condition_value_matches(actual, expected):
            return False
    return True


def _lookup_context(context: Mapping[str, Any], key: str) -> Any:
    value: Any = context
    for part in key.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _condition_value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping) and set(expected) & _CONDITION_OPERATORS:
        for operator, operand in expected.items():
            if operator not in _CONDITION_OPERATORS:
                return False
            if not _operator_matches(operator, actual, operand):
                return False
        return True
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        return _conditions_match(expected, actual)
    return actual is not _MISSING and actual == expected


def _operator_matches(operator: str, actual: Any, operand: Any) -> bool:
    if operator == "exists":
        return (actual is not _MISSING) is bool(operand)
    if actual is _MISSING:
        return False
    if operator in {"eq", "equals"}:
        return bool(actual == operand)
    if operator == "not_equals":
        return bool(actual != operand)
    if operator in {"in", "not_in"}:
        if not isinstance(operand, Sequence) or isinstance(operand, (str, bytes)):
            return False
        result = actual in operand
        return not result if operator == "not_in" else result
    if operator == "contains":
        try:
            return operand in actual
        except TypeError:
            return False
    if operator in {"contains_any", "contains_all"}:
        if not isinstance(operand, Sequence) or isinstance(operand, (str, bytes)):
            return False
        try:
            matches = [value in actual for value in operand]
        except TypeError:
            return False
        return any(matches) if operator == "contains_any" else all(matches)
    if operator == "matches":
        return isinstance(actual, str) and isinstance(operand, str) and fnmatchcase(actual, operand)
    return False
