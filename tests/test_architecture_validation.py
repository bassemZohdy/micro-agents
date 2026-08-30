"""Architecture Validation — verify examples demonstrate Micro-Agent properties."""

from pathlib import Path

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_file
from runtimes.adk import AdkRuntime

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


class TestResidencyRenewalAgent:
    """Validate Residency Eligibility Agent example."""

    @pytest.fixture
    def definition(self):
        return load_definition_from_file(EXAMPLES_DIR / "residency-renewal.yaml")

    def test_bounded_responsibility(self, definition):
        """Agent has bounded agentic capability."""
        assert definition.metadata.name == "residency-renewal"
        skills = definition.spec.dependencies.skills
        assert all("residency" in s.tags for s in skills)

    def test_explicit_identity(self, definition):
        """Agent has explicit identity."""
        assert definition.metadata.name
        assert definition.metadata.version

    def test_explicit_skills(self, definition):
        """Agent has explicit skills."""
        skills = definition.spec.dependencies.skills
        assert len(skills) == 3
        skill_ids = {s.id for s in skills}
        assert "check-eligibility" in skill_ids
        assert "submit-renewal" in skill_ids
        assert "check-renewal-status" in skill_ids

    def test_external_state(self, definition):
        """Agent externalizes state."""
        assert definition.spec.dependencies.memory is not None
        assert definition.spec.dependencies.session.persistence == "external"

    def test_mcp_integration(self, definition):
        """Agent integrates MCP."""
        assert len(definition.spec.dependencies.mcp_servers) == 2

    def test_observability_ready(self, definition):
        """Definition supports observability."""
        assert definition.metadata.labels
        assert definition.metadata.annotations

    def test_independent_deployment(self, definition):
        """Agent is independently deployable."""
        assert definition.api_version == "microagents.io/v1alpha1"
        assert definition.kind == "MicroAgent"

    @pytest.mark.asyncio
    async def test_invocation(self, definition):
        """Agent can be invoked through the runtime."""
        runtime = AdkRuntime()
        agent = await runtime.create(definition)
        request = AgentRequest(input={"action": "check-eligibility", "user_id": "u1"})
        response = await runtime.invoke(agent, request)
        assert response.status == "success"


class TestNotificationAgent:
    """Validate Notification Agent example."""

    @pytest.fixture
    def definition(self):
        return load_definition_from_file(EXAMPLES_DIR / "notification-agent.yaml")

    def test_bounded_responsibility(self, definition):
        """Agent has bounded agentic capability."""
        assert definition.metadata.name == "notification-agent"
        assert definition.spec.dependencies.skills[0].id == "send-notification"

    def test_independent_scaling(self, definition):
        """Agent supports independent scaling."""
        assert definition.metadata.version

    def test_explicit_skills(self, definition):
        """Agent has explicit skills."""
        assert len(definition.spec.dependencies.skills) == 1

    @pytest.mark.asyncio
    async def test_invocation(self, definition):
        """Agent can be invoked through the runtime."""
        runtime = AdkRuntime()
        agent = await runtime.create(definition)
        request = AgentRequest(input={"action": "send", "message": "hello"})
        response = await runtime.invoke(agent, request)
        assert response.status == "success"


class TestArchitectureProperties:
    """Validate that the architecture supports key properties."""

    def test_examples_are_independent(self):
        """Two examples are independent Micro-Agents."""
        residency = load_definition_from_file(EXAMPLES_DIR / "residency-renewal.yaml")
        notification = load_definition_from_file(EXAMPLES_DIR / "notification-agent.yaml")
        assert residency.metadata.name != notification.metadata.name
        assert residency.spec.dependencies.skills != notification.spec.dependencies.skills

    def test_containerization_support(self):
        """Dockerfile exists for containerization."""
        dockerfile = Path(__file__).parent.parent / "Dockerfile"
        assert dockerfile.exists()

    def test_kubernetes_support(self):
        """Kubernetes manifests exist."""
        k8s_dir = Path(__file__).parent.parent / "deploy" / "kubernetes"
        assert (k8s_dir / "deployment.yaml").exists()
        assert (k8s_dir / "service.yaml").exists()
        assert (k8s_dir / "configmap.yaml").exists()
