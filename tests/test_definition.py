"""Tests for Micro-Agent Definition v1alpha1."""

from pathlib import Path

import pytest

from micro_agent.definition import (
    DefinitionError,
    load_definition_from_dict,
    load_definition_from_file,
    load_definition_from_yaml,
)

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


class TestMinimalDefinition:
    """Test minimal valid definitions."""

    def test_minimal_definition_loads(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test-agent", "version": "0.1.0"},
            "spec": {
                "behavior": {"instructions": "Do something."},
            },
        }
        definition = load_definition_from_dict(data)
        assert definition.metadata.name == "test-agent"
        assert definition.metadata.version == "0.1.0"
        assert definition.spec.behavior.instructions == "Do something."
        assert definition.api_version == "microagents.io/v1alpha1"
        assert definition.kind == "MicroAgent"

    def test_full_definition_loads(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {
                "name": "residency-renewal",
                "version": "1.0.0",
                "description": "Handles residency renewal.",
                "labels": {"domain": "residency"},
                "annotations": {"owner": "team@example.com"},
            },
            "spec": {
                "behavior": {
                    "instructions": "Assist with residency renewal.",
                    "input_contract": {
                        "parameters": [{"name": "user_id", "type": "string", "required": True}]
                    },
                    "output_contract": {
                        "parameters": [{"name": "result", "type": "object", "required": True}]
                    },
                },
                "dependencies": {
                    "model": {"ref": "reasoning-model", "provider": "anthropic"},
                    "tools": [{"name": "check_eligibility", "source": "native"}],
                    "mcp_servers": [{"ref": "residency-services"}],
                    "skills": [
                        {
                            "id": "check-eligibility",
                            "name": "Check Eligibility",
                            "tags": ["residency"],
                        }
                    ],
                    "knowledge": [{"ref": "residency-rules"}],
                    "memory": {"ref": "residency-memory", "scope": "user"},
                    "session": {"persistence": "external", "ttl_seconds": 3600},
                },
                "runtime": {
                    "timeout_seconds": 120,
                    "max_iterations": 10,
                    "error_policy": "retry",
                    "capabilities": ["streaming"],
                },
                "interoperability": {
                    "a2a": {"enabled": True, "endpoint": "https://a2a.example.com"}
                },
                "security": {
                    "credential_refs": ["api-key"],
                    "policy_refs": ["access-policy"],
                },
            },
        }
        definition = load_definition_from_dict(data)
        assert definition.spec.dependencies.model is not None
        assert definition.spec.dependencies.model.ref == "reasoning-model"
        assert len(definition.spec.dependencies.skills) == 1
        assert definition.spec.runtime.error_policy.value == "retry"
        assert definition.spec.interoperability.a2a.enabled is True


class TestInvalidDefinitions:
    """Test that invalid definitions fail with useful diagnostics."""

    def test_missing_metadata_fails(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "spec": {"behavior": {"instructions": "Do something."}},
        }
        with pytest.raises(DefinitionError) as exc_info:
            load_definition_from_dict(data)
        assert "error(s)" in str(exc_info.value)
        assert exc_info.value.errors

    def test_missing_instructions_fails(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"behavior": {}},
        }
        with pytest.raises(DefinitionError) as exc_info:
            load_definition_from_dict(data)
        assert exc_info.value.errors

    def test_wrong_api_version_fails(self):
        data = {
            "apiVersion": "wrong/v1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"behavior": {"instructions": "Do something."}},
        }
        with pytest.raises(DefinitionError):
            load_definition_from_dict(data)

    def test_unknown_properties_rejected(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Do something."},
                "unknown_field": "should fail",
            },
        }
        with pytest.raises(DefinitionError):
            load_definition_from_dict(data)

    def test_invalid_yaml_fails(self):
        with pytest.raises(DefinitionError, match="Invalid YAML"):
            load_definition_from_yaml("{{invalid yaml")

    def test_non_dict_yaml_fails(self):
        with pytest.raises(DefinitionError, match="mapping"):
            load_definition_from_yaml("- just a list")


class TestSemanticValidation:
    """Definition-level constraints that JSON Schema alone cannot express."""

    def _base(self) -> dict[str, object]:
        return {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "semantic-agent", "version": "1.0.0"},
            "spec": {"behavior": {"instructions": "Validate this definition."}},
        }

    def test_semver_and_name_format_are_enforced(self):
        data = self._base()
        data["metadata"] = {"name": "Not A Name", "version": "1"}
        with pytest.raises(DefinitionError):
            load_definition_from_dict(data)

    def test_model_alias_and_provider_id_are_distinct(self):
        data = self._base()
        data["spec"] = {
            "behavior": {"instructions": "Validate this definition."},
            "dependencies": {
                "model": {
                    "ref": "logical-reasoning",
                    "model_id": "provider-model-v2",
                    "provider": "openai-compatible",
                    "endpoint": "https://llm.example.com/v1",
                }
            },
        }
        definition = load_definition_from_dict(data)
        assert definition.spec.dependencies.model is not None
        assert definition.spec.dependencies.model.ref == "logical-reasoning"
        assert definition.spec.dependencies.model.model_id == "provider-model-v2"

    def test_duplicate_dependency_names_are_rejected(self):
        data = self._base()
        data["spec"] = {
            "behavior": {"instructions": "Validate this definition."},
            "dependencies": {
                "tools": [{"name": "echo"}, {"name": "echo"}],
                "skills": [{"id": "greet", "name": "Greet"}, {"id": "greet", "name": "Again"}],
            },
        }
        with pytest.raises(DefinitionError, match="unique"):
            load_definition_from_dict(data)

    def test_duplicate_contract_parameters_are_rejected(self):
        data = self._base()
        data["spec"] = {
            "behavior": {
                "instructions": "Validate this definition.",
                "input_contract": {
                    "parameters": [
                        {"name": "value", "type": "string"},
                        {"name": "value", "type": "string"},
                    ]
                },
            }
        }
        with pytest.raises(DefinitionError, match="unique"):
            load_definition_from_dict(data)

    def test_mcp_transport_requires_matching_endpoint(self):
        data = self._base()
        data["spec"] = {
            "behavior": {"instructions": "Validate this definition."},
            "dependencies": {
                "mcp_servers": [
                    {"ref": "local", "transport": "stdio", "endpoint": "https://example.com"}
                ]
            },
        }
        with pytest.raises(DefinitionError, match="stdio"):
            load_definition_from_dict(data)

    def test_mcp_http_transport_requires_valid_url(self):
        data = self._base()
        data["spec"] = {
            "behavior": {"instructions": "Validate this definition."},
            "dependencies": {
                "mcp_servers": [
                    {"ref": "remote", "transport": "streamable-http", "endpoint": "not-a-url"}
                ]
            },
        }
        with pytest.raises(DefinitionError, match="URL"):
            load_definition_from_dict(data)


class TestYamlLoading:
    """Test YAML loading."""

    def test_load_from_yaml_string(self):
        yaml_content = """
apiVersion: microagents.io/v1alpha1
kind: MicroAgent
metadata:
  name: yaml-agent
  version: 0.1.0
spec:
  behavior:
    instructions: Do something from YAML.
"""
        definition = load_definition_from_yaml(yaml_content)
        assert definition.metadata.name == "yaml-agent"

    def test_load_from_file(self, tmp_path):
        yaml_content = """
apiVersion: microagents.io/v1alpha1
kind: MicroAgent
metadata:
  name: file-agent
  version: 0.1.0
spec:
  behavior:
    instructions: Loaded from file.
"""
        file_path = tmp_path / "agent.yaml"
        file_path.write_text(yaml_content)
        definition = load_definition_from_file(file_path)
        assert definition.metadata.name == "file-agent"

    def test_load_nonexistent_file_fails(self):
        with pytest.raises(DefinitionError, match="not found"):
            load_definition_from_file("/nonexistent/agent.yaml")


class TestExampleDefinitions:
    """Test that example definitions load successfully."""

    def test_residency_renewal_example(self):
        path = EXAMPLES_DIR / "residency-renewal.yaml"
        definition = load_definition_from_file(path)
        assert definition.metadata.name == "residency-renewal"
        assert definition.spec.dependencies.model is not None
        assert len(definition.spec.dependencies.skills) == 3

    def test_notification_agent_example(self):
        path = EXAMPLES_DIR / "notification-agent.yaml"
        definition = load_definition_from_file(path)
        assert definition.metadata.name == "notification-agent"


class TestNoAdkTypes:
    """Verify definition contains no ADK-native types."""

    def test_model_fields_are_runtime_neutral(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"behavior": {"instructions": "Test."}},
        }
        definition = load_definition_from_dict(data)
        serialized = definition.model_dump()
        serialized_str = str(serialized)
        adk_indicators = ["google.adk", "adk_agent", "LlmAgent", "google.adk.agents"]
        for indicator in adk_indicators:
            assert indicator not in serialized_str, f"ADK type leaked: {indicator}"


class TestSchema:
    """Test JSON Schema generation."""

    def test_schema_generates(self):
        from micro_agent.definition.schema import generate_schema

        schema = generate_schema()
        assert "$schema" in schema
        assert "$id" in schema
        assert "properties" in schema
