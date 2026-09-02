from micro_agent.definition.models import OutputContract, ParameterDefinition
from micro_agent.models import (
    OpenAICompatConfig,
    OpenAICompatProvider,
    ProviderCapabilities,
    output_contract_json_schema,
    structured_output_generation,
)
from runtimes.adk.runtime import AdkRuntime, AdkRuntimeConfig


def _contract() -> OutputContract:
    return OutputContract(
        parameters=[
            ParameterDefinition(name="answer", type="string", required=True),
            ParameterDefinition(name="confidence", type="number", required=False),
        ]
    )


def test_output_contract_translates_to_json_schema() -> None:
    schema = output_contract_json_schema(_contract())
    assert schema == {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "additionalProperties": False,
        "required": ["answer"],
    }


def test_structured_generation_requires_provider_capability() -> None:
    unsupported = structured_output_generation({}, _contract(), ProviderCapabilities())
    assert "response_format" not in unsupported

    supported = structured_output_generation(
        {"temperature": 0.1},
        _contract(),
        ProviderCapabilities(structured_output=True),
    )
    assert supported["temperature"] == 0.1
    assert supported["response_format"]["type"] == "json_schema"
    assert supported["response_format"]["json_schema"]["strict"] is True


def test_explicit_response_format_is_preserved() -> None:
    explicit = {"response_format": {"type": "json_object"}}
    result = structured_output_generation(
        explicit,
        _contract(),
        ProviderCapabilities(structured_output=True),
    )
    assert result == explicit


def test_openai_compat_and_builtin_runtime_advertise_structured_output() -> None:
    provider = OpenAICompatProvider(OpenAICompatConfig(endpoint="https://llm.example.test/v1"))
    assert provider.capabilities().structured_output is True

    runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
    assert runtime.capabilities().structured_output is True
