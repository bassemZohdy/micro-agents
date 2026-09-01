"""A2A integration: standard card route, v1 card contract, and an official
SDK client resolving the card and completing a non-streaming task."""

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

pytest.importorskip("a2a")


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
                "interoperability": {"a2a": {"enabled": True, "protocol_version": "0.3.0"}},
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
        assert card.protocol_version == "0.3.0"
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
    """The standard well-known agent-card endpoint serves the v1 card."""

    def _app(self):
        return create_app(
            DefaultMicroAgent(_definition(), AdkRuntime()), base_url="https://agent.example.com"
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
            response = await client.get(a2a_well_known_path())
        card = response.json()

        # A2A agent-card contract checks (no framework types involved).
        assert card["name"] == "residency-renewal"
        assert card["protocolVersion"] == "0.3.0"
        assert card["preferredTransport"] == "JSONRPC"
        assert card["url"].startswith("https://")
        assert isinstance(card["capabilities"], dict)
        assert isinstance(card["skills"], list) and card["skills"]
        for skill in card["skills"]:
            assert skill["id"]
            assert skill["name"]
            assert isinstance(skill["tags"], list)
        assert card["defaultInputModes"] == ["application/json"]
        assert card["defaultOutputModes"] == ["application/json"]


class TestOfficialSdkInterop:
    """The official a2a-sdk client resolves the card and completes a task."""

    @pytest.mark.asyncio
    async def test_official_client_resolves_card_and_completes_task(self):
        """An official SDK client resolves the card and completes a task."""
        import json as jsonlib

        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.client.helpers import create_text_message_object
        from a2a.types import Role, TaskState, TextPart
        from a2a.types import TransportProtocol as TransportProtocolType

        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=FakeModelProvider(FakeModelConfig(response="renewal done"))
            )
        )
        agent = DefaultMicroAgent(_definition(), runtime)
        await agent.initialize()
        await agent.start()
        app = create_app(agent, base_url="http://test")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            resolver = A2ACardResolver(httpx_client=http_client, base_url="http://test")
            card = await resolver.get_agent_card()
            assert card.name == "residency-renewal"

            config = ClientConfig(
                httpx_client=http_client,
                streaming=False,
                supported_transports=[TransportProtocolType.jsonrpc],
            )
            client = ClientFactory(config).create(card)
            message = create_text_message_object(
                content=jsonlib.dumps({"action": "renew"}), role=Role.user
            )
            final_task = None
            async for task, _update in client.send_message(message):
                final_task = task
            assert final_task is not None
            assert final_task.status.state == TaskState.completed
            artifacts = final_task.artifacts or []
            assert artifacts
            texts = [
                part.root.text for part in artifacts[0].parts if isinstance(part.root, TextPart)
            ]
            assert texts and texts[0]
            await agent.stop()
            await agent.shutdown()
