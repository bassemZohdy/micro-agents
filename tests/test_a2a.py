"""A2A card model and version negotiation unit tests (official a2a-sdk)."""

import pytest

from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability.a2a import (
    SUPPORTED_PROTOCOL_VERSIONS,
    UnsupportedProtocolVersionError,
    a2a_well_known_path,
    agent_card_from_definition,
    normalize_protocol_version,
    skills_mapping,
)

pytest.importorskip("a2a")


def _definition(**a2a_overrides):
    a2a = {"enabled": True, **a2a_overrides}
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "card-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "skills": [
                        {
                            "id": "check",
                            "name": "Check",
                            "description": "Check things.",
                            "tags": ["check"],
                        }
                    ],
                },
                "interoperability": {"a2a": a2a},
            },
        }
    )


class TestWellKnownPath:
    def test_standard_agent_card_path(self):
        assert a2a_well_known_path() == "/.well-known/agent-card.json"


class TestProtocolVersion:
    def test_supported_versions_declared_explicitly(self):
        assert frozenset({"0.3.0"}) == SUPPORTED_PROTOCOL_VERSIONS

    def test_default_version_is_normalized(self):
        assert normalize_protocol_version(None) == "0.3.0"

    def test_unsupported_version_rejected(self):
        with pytest.raises(UnsupportedProtocolVersionError, match="9.9"):
            normalize_protocol_version("9.9")

    def test_card_declares_normalized_version(self):
        card = agent_card_from_definition(_definition())
        assert card.protocol_version == "0.3.0"


class TestAgentCard:
    def test_v1_card_model_fields(self):
        card = agent_card_from_definition(_definition(), base_url="https://agent.example.com")
        assert card.name == "card-agent"
        assert card.version == "1.0.0"
        assert card.url == "https://agent.example.com"
        assert card.preferred_transport == "JSONRPC"
        assert card.capabilities.streaming is False
        assert card.capabilities.push_notifications is False
        assert card.default_input_modes == ["application/json"]
        assert card.default_output_modes == ["application/json"]
        assert [s.id for s in card.skills] == ["check"]

    def test_card_url_falls_back_to_a2a_endpoint(self):
        definition = _definition(endpoint="https://a2a.example.com")
        card = agent_card_from_definition(definition)
        assert card.url == "https://a2a.example.com"

    def test_security_scheme_advertised(self):
        from a2a.types import OpenIdConnectSecurityScheme

        card = agent_card_from_definition(
            _definition(),
            base_url="https://agent.example.com",
            security_scheme={
                "type": "openIdConnect",
                "open_id_connect_url": "https://idp/.well-known/openid-configuration",
            },
        )
        assert card.security == [{"oidc": []}]
        scheme = card.security_schemes["oidc"]
        assert isinstance(scheme.root, OpenIdConnectSecurityScheme)
        assert scheme.root.open_id_connect_url == "https://idp/.well-known/openid-configuration"

    def test_skills_mapping(self):
        skills = skills_mapping(_definition())
        assert skills[0].name == "Check"
        assert skills[0].tags == ["check"]
