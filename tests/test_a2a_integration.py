"""A2A: AgentCard generation, skills mapping, well-known endpoint.

The "independent client" test validates the served agent card as raw JSON
against the A2A card contract — it deliberately does not import the
AgentCard/AgentSkill classes, simulating a third-party consumer.
"""

import httpx
import pytest

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.interoperability.a2a import (
    a2a_well_known_path,
    agent_card_from_definition,
    skills_mapping,
)
from runtimes.adk import AdkRuntime

pytestmark = pytest.mark.integration


def _definition() -> object:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {
                "name": "residency-renewal",
                "version": "1.0.0",
                "description": "Handles residency renewal activities.",
                "labels": {"domain": "residency"},
            },
            "spec": {
                "behavior": {"instructions": "Assist with renewals."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "skills": [
                        {
                            "id": "check-eligibility",
                            "name": "Check Eligibility",
                            "description": "Determine renewal eligibility.",
                            "tags": ["residency", "eligibility"],
                        },
                        {
                            "id": "submit-renewal",
                            "name": "Submit Renewal",
                            "description": "Submit a renewal application.",
                            "tags": ["residency"],
                        },
                    ],
                },
                "interoperability": {"a2a": {"enabled": True, "protocol_version": "1.0"}},
                "security": {"identity_requirements": {"require_caller_identity": True}},
            },
        }
    )


class TestSkillsMapping:
    """Definition skills map to A2A AgentSkill shapes."""

    def test_skills_mapping(self):
        definition = _definition()
        skills = skills_mapping(definition)
        assert [s.id for s in skills] == ["check-eligibility", "submit-renewal"]
        assert skills[0].name == "Check Eligibility"
        assert skills[0].tags == ["residency", "eligibility"]


class TestAgentCardGeneration:
    """AgentCard generated from a definition."""

    def test_card_fields(self):
        definition = _definition()
        card = agent_card_from_definition(definition, base_url="https://agent.example.com")
        assert card.name == "residency-renewal"
        assert card.version == "1.0.0"
        assert card.url == "https://agent.example.com"
        assert card.security == {"callerIdentityRequired": True}
        assert card.metadata["protocolVersion"] == "1.0"
        assert len(card.skills) == 2

    def test_card_url_falls_back_to_a2a_endpoint(self):
        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "a", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "x"},
                    "interoperability": {
                        "a2a": {"enabled": True, "endpoint": "https://a2a.example.com"}
                    },
                },
            }
        )
        card = agent_card_from_definition(definition)
        assert card.url == "https://a2a.example.com"


class TestAgentCardEndpoint:
    """The well-known agent-card endpoint serves the card."""

    def _app(self):
        from micro_agent.security import (
            AuthenticatedIdentity,
            Authenticator,
            CallerIdentity,
        )

        class _StubAuthenticator(Authenticator):
            async def authenticate(self, headers):
                return AuthenticatedIdentity(caller=CallerIdentity(caller_id="caller-1"))

        definition = _definition()
        agent = DefaultMicroAgent(definition, AdkRuntime())
        # The definition demands caller identity, so the app must be created
        # with an authenticator configured.
        return create_app(
            agent,
            base_url="https://agent.example.com",
            authenticator=_StubAuthenticator(),
        )

    @pytest.mark.asyncio
    async def test_endpoint_serves_card(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(a2a_well_known_path())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_independent_client_validates_card(self):
        """A third-party client fetches and validates the card as raw JSON."""
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/.well-known/agent.json")
        card = response.json()

        # A2A agent-card contract checks (no framework types involved).
        assert isinstance(card["name"], str) and card["name"]
        assert card["name"] == "residency-renewal"
        assert isinstance(card["version"], str) and card["version"]
        assert isinstance(card["description"], str)
        assert card["url"].startswith("https://")
        assert isinstance(card["capabilities"], dict)
        assert isinstance(card["skills"], list) and card["skills"]
        for skill in card["skills"]:
            assert isinstance(skill["id"], str) and skill["id"]
            assert isinstance(skill["name"], str) and skill["name"]
            assert isinstance(skill["description"], str)
            assert isinstance(skill["tags"], list)
        assert card["security"]["callerIdentityRequired"] is True
