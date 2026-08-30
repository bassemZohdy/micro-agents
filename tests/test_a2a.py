"""Tests for Micro-Agent A2A interoperability."""

from micro_agent.interoperability import (
    A2AConfig,
    A2AMessage,
    A2AResponse,
    A2ATask,
    AgentCard,
    AgentSkill,
)


class TestAgentSkill:
    """Test agent skill for A2A."""

    def test_creation(self):
        skill = AgentSkill(id="check", name="Check Eligibility")
        assert skill.id == "check"
        assert skill.tags == []


class TestAgentCard:
    """Test A2A Agent Card."""

    def test_basic_card(self):
        card = AgentCard(name="test-agent")
        assert card.name == "test-agent"
        assert card.skills == []

    def test_card_with_skills(self):
        skills = [
            AgentSkill(id="check", name="Check"),
            AgentSkill(id="submit", name="Submit"),
        ]
        card = AgentCard(name="residency-agent", skills=skills)
        assert len(card.skills) == 2


class TestA2AMessage:
    """Test A2A message."""

    def test_default(self):
        msg = A2AMessage()
        assert msg.role == "user"
        assert msg.parts == []

    def test_with_parts(self):
        msg = A2AMessage(
            role="user",
            parts=[{"type": "text", "text": "check eligibility"}],
        )
        assert len(msg.parts) == 1


class TestA2ATask:
    """Test A2A task."""

    def test_creation(self):
        task = A2ATask(task_id="t1")
        assert task.task_id == "t1"
        assert task.messages == []


class TestA2AResponse:
    """Test A2A response."""

    def test_creation(self):
        resp = A2AResponse(task_id="t1", status="completed")
        assert resp.status == "completed"


class TestA2AConfig:
    """Test A2A configuration."""

    def test_defaults(self):
        config = A2AConfig()
        assert config.enabled is False
        assert config.endpoint is None

    def test_enabled(self):
        config = A2AConfig(enabled=True, endpoint="https://a2a.example.com")
        assert config.enabled is True
