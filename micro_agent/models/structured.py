"""Runtime-neutral structured-output helpers."""

from __future__ import annotations

from typing import Any

from micro_agent.definition import OutputContract
from micro_agent.models.model import ProviderCapabilities


def output_contract_json_schema(contract: OutputContract) -> dict[str, Any]:
    """Translate a Micro-Agent output contract to a JSON Schema object."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter in contract.parameters:
        schema: dict[str, Any]
        if parameter.type == "any":
            schema = {}
        elif parameter.type == "array":
            schema = {"type": "array", "items": {}}
        elif parameter.type == "object":
            schema = {"type": "object", "additionalProperties": True}
        else:
            schema = {"type": parameter.type}
        if parameter.description:
            schema["description"] = parameter.description
        if parameter.default is not None:
            schema["default"] = parameter.default
        properties[parameter.name] = schema
        if parameter.required:
            required.append(parameter.name)

    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def structured_output_generation(
    generation: dict[str, Any],
    contract: OutputContract,
    capabilities: ProviderCapabilities,
) -> dict[str, Any]:
    """Add provider-native JSON-schema output constraints when supported.

    Explicit provider generation settings win. Providers that do not
    advertise structured output are never sent a response_format field.
    """
    result = dict(generation)
    if not contract.parameters or not capabilities.structured_output:
        return result
    result.setdefault(
        "response_format",
        {
            "type": "json_schema",
            "json_schema": {
                "name": "micro_agent_output",
                "strict": True,
                "schema": output_contract_json_schema(contract),
            },
        },
    )
    return result
