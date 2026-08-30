"""Micro-Agent Definition Portability Review.

Verifies that the definition contains no ADK-specific leakage
and documents mandatory vs optional semantics.
"""

from pathlib import Path

from micro_agent.definition import (
    MicroAgentDefinition,
    load_definition_from_dict,
    load_definition_from_file,
)

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


class TestDefinitionPortability:
    """Verify definition portability — no ADK-specific leakage."""

    def test_no_adk_types_in_model_fields(self):
        """No ADK-native types in model field names."""
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"behavior": {"instructions": "Test."}},
        }
        load_definition_from_dict(data)
        schema = MicroAgentDefinition.model_json_schema()
        schema_str = str(schema).lower()
        adk_indicators = ["google.adk", "adk_agent", "llm_agent", "google.adk.agents"]
        for indicator in adk_indicators:
            assert indicator not in schema_str

    def test_no_adk_types_in_serialized_definition(self):
        """Serialized definitions contain no ADK references."""
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test."},
                "dependencies": {"model": {"ref": "test-model"}},
            },
        }
        definition = load_definition_from_dict(data)
        serialized = definition.model_dump()
        serialized_str = str(serialized).lower()
        assert "adk" not in serialized_str
        assert "google.adk" not in serialized_str

    def test_examples_are_portable(self):
        """Example definitions are portable across runtimes."""
        for example_file in EXAMPLES_DIR.glob("*.yaml"):
            definition = load_definition_from_file(example_file)
            serialized = str(definition.model_dump()).lower()
            assert "adk" not in serialized, f"{example_file.name} contains ADK reference"

    def test_mandatory_semantics_present(self):
        """Definition contains mandatory semantics."""
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test."},
                "dependencies": {
                    "model": {"ref": "model"},
                    "skills": [{"id": "s1", "name": "Skill 1"}],
                },
            },
        }
        definition = load_definition_from_dict(data)
        assert definition.metadata.name
        assert definition.metadata.version
        assert definition.spec.behavior.instructions
        assert definition.spec.dependencies.model
        assert definition.spec.dependencies.skills

    def test_optional_semantics_have_defaults(self):
        """Optional semantics have sensible defaults."""
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"behavior": {"instructions": "Test."}},
        }
        definition = load_definition_from_dict(data)
        assert definition.spec.runtime.timeout_seconds is None
        assert definition.spec.interoperability.a2a.enabled is False
        assert definition.spec.security.credential_refs == []
