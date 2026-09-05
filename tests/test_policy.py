"""Tests for Micro-Agent Bounded Autonomy and Policy."""

from micro_agent.observability import (
    AgentPolicy,
    PolicyEffect,
    PolicyEvaluator,
    PolicyRule,
)
from micro_agent.security import (
    CallerIdentity,
    InvocationIdentity,
    UserContext,
    invocation_identity,
)


class TestPolicyEffect:
    """Test policy effect."""

    def test_values(self):
        assert PolicyEffect.ALLOW == "allow"
        assert PolicyEffect.DENY == "deny"


class TestPolicyRule:
    """Test policy rule."""

    def test_creation(self):
        rule = PolicyRule(
            effect=PolicyEffect.ALLOW,
            resource="skill:check",
            actions=["invoke"],
        )
        assert rule.effect == PolicyEffect.ALLOW


class TestAgentPolicy:
    """Test agent policy."""

    def test_defaults(self):
        policy = AgentPolicy()
        assert policy.is_skill_allowed("any-skill") is True
        assert policy.is_tool_allowed("any-tool") is True

    def test_denied_skill(self):
        policy = AgentPolicy(denied_skills=["dangerous-skill"])
        assert policy.is_skill_allowed("dangerous-skill") is False
        assert policy.is_skill_allowed("safe-skill") is True

    def test_allowed_skills_whitelist(self):
        policy = AgentPolicy(allowed_skills=["check", "submit"])
        assert policy.is_skill_allowed("check") is True
        assert policy.is_skill_allowed("unknown") is False

    def test_denied_tool(self):
        policy = AgentPolicy(denied_tools=["delete_all"])
        assert policy.is_tool_allowed("delete_all") is False
        assert policy.is_tool_allowed("read") is True

    def test_allowed_tools_whitelist(self):
        policy = AgentPolicy(allowed_tools=["echo", "check"])
        assert policy.is_tool_allowed("echo") is True
        assert policy.is_tool_allowed("other") is False

    def test_denied_mcp(self):
        policy = AgentPolicy(denied_mcps=["untrusted-mcp"])
        assert policy.is_mcp_allowed("untrusted-mcp") is False
        assert policy.is_mcp_allowed("trusted-mcp") is True

    def test_side_effect_policy(self):
        policy = AgentPolicy(side_effect_policy="deny")
        assert policy.side_effect_policy == "deny"


class TestPolicyEvaluator:
    """Test policy evaluator."""

    def test_skill_allowed(self):
        policy = AgentPolicy()
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_skill("check")
        assert decision.allowed is True

    def test_skill_denied(self):
        policy = AgentPolicy(denied_skills=["dangerous"])
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_skill("dangerous")
        assert decision.allowed is False
        assert "denied" in decision.reason.lower()

    def test_tool_allowed(self):
        policy = AgentPolicy()
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_tool("echo")
        assert decision.allowed is True

    def test_tool_denied(self):
        policy = AgentPolicy(denied_tools=["rm"])
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_tool("rm")
        assert decision.allowed is False

    def test_mcp_allowed(self):
        policy = AgentPolicy()
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_mcp("services")
        assert decision.allowed is True

    def test_side_effect_allowed(self):
        policy = AgentPolicy(side_effect_policy="allow")
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_side_effect("payment")
        assert decision.allowed is True

    def test_side_effect_denied(self):
        policy = AgentPolicy(side_effect_policy="deny")
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_side_effect("payment")
        assert decision.allowed is False

    def test_side_effect_requires_approval(self):
        policy = AgentPolicy(approval_required=True)
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_side_effect("payment")
        assert decision.allowed is False
        assert "approval" in decision.reason.lower()

    def test_model_allowed_without_restrictions(self):
        policy = AgentPolicy()
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_model("gpt-4o", "gpt-4o", "openai")
        assert decision.allowed is True

    def test_model_denied_by_deny_list(self):
        policy = AgentPolicy(model_restrictions={"denied_models": ["gpt-4o"]})
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_model("reasoning-model", "gpt-4o", "openai")
        assert decision.allowed is False

    def test_model_denied_when_not_in_allow_list(self):
        policy = AgentPolicy(model_restrictions={"allowed_models": ["claude-3"]})
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_model("reasoning-model", "gpt-4o", "openai")
        assert decision.allowed is False

    def test_model_allowed_by_ref_match_in_allow_list(self):
        policy = AgentPolicy(model_restrictions={"allowed_models": ["reasoning-model"]})
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_model("reasoning-model", "gpt-4o", "openai")
        assert decision.allowed is True

    def test_model_provider_denied(self):
        policy = AgentPolicy(model_restrictions={"denied_providers": ["anthropic"]})
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_model("reasoning-model", None, "anthropic")
        assert decision.allowed is False
        assert "anthropic" in decision.reason

    def test_model_provider_not_in_allow_list(self):
        policy = AgentPolicy(model_restrictions={"allowed_providers": ["openai"]})
        evaluator = PolicyEvaluator(policy)
        decision = evaluator.evaluate_model("reasoning-model", None, "anthropic")
        assert decision.allowed is False

    def test_conditional_rule_matches_context_and_skips_non_matching_context(self):
        policy = AgentPolicy(
            rules=[
                PolicyRule(
                    effect=PolicyEffect.DENY,
                    resource="tool:charge-card",
                    actions=["invoke"],
                    conditions={"tenant_id": "restricted"},
                )
            ]
        )
        evaluator = PolicyEvaluator(policy)

        denied = evaluator.evaluate_tool("charge-card", context={"tenant_id": "restricted"})
        allowed = evaluator.evaluate_tool("charge-card", context={"tenant_id": "public"})

        assert denied.allowed is False
        assert denied.rule is policy.rules[0]
        assert allowed.allowed is True
        assert allowed.rule is None

    def test_condition_operators_and_resource_wildcards_are_supported(self):
        policy = AgentPolicy(
            rules=[
                PolicyRule(
                    effect=PolicyEffect.ALLOW,
                    resource="tool:*",
                    actions=["invoke"],
                    conditions={
                        "user.tenant_id": {"equals": "trusted"},
                        "roles": {"contains_all": ["operator", "auditor"]},
                        "caller_type": {"in": ["user", "service"]},
                    },
                )
            ]
        )
        evaluator = PolicyEvaluator(policy)

        decision = evaluator.evaluate_tool(
            "lookup",
            context={
                "user": {"tenant_id": "trusted"},
                "roles": ["operator", "auditor", "reviewer"],
                "caller_type": "service",
            },
        )

        assert decision.allowed is True
        assert decision.rule is policy.rules[0]

    def test_matching_deny_rule_wins_over_matching_allow_rule(self):
        policy = AgentPolicy(
            rules=[
                PolicyRule(
                    effect=PolicyEffect.ALLOW,
                    resource="tool:delete",
                    actions=["invoke"],
                    conditions={"roles": {"contains": "operator"}},
                ),
                PolicyRule(
                    effect=PolicyEffect.DENY,
                    resource="tool:delete",
                    actions=["invoke"],
                    conditions={"tenant_id": "restricted"},
                ),
            ]
        )
        decision = PolicyEvaluator(policy).evaluate_tool(
            "delete",
            context={"roles": ["operator"], "tenant_id": "restricted"},
        )

        assert decision.allowed is False
        assert decision.rule is policy.rules[1]

    def test_verified_invocation_identity_overrides_untrusted_context_values(self):
        policy = AgentPolicy(
            rules=[
                PolicyRule(
                    effect=PolicyEffect.DENY,
                    resource="tool:pay",
                    actions=["invoke"],
                    conditions={"tenant_id": "verified-tenant"},
                )
            ]
        )

        with invocation_identity(
            InvocationIdentity(
                caller=CallerIdentity(caller_id="caller-1"),
                user=UserContext(user_id="user-1", tenant_id="verified-tenant"),
            )
        ):
            decision = PolicyEvaluator(policy).evaluate_tool(
                "pay", context={"tenant_id": "spoofed-tenant"}
            )

        assert decision.allowed is False
