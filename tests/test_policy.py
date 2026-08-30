"""Tests for Micro-Agent Bounded Autonomy and Policy."""

from micro_agent.observability import (
    AgentPolicy,
    PolicyEffect,
    PolicyEvaluator,
    PolicyRule,
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
