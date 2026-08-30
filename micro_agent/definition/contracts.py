"""Runtime validation for declarative input and output contracts.

The definition model describes contracts in a runtime-neutral way. This module
keeps enforcement runtime-neutral so adapters can reject invalid requests
before model invocation and invalid responses before they cross the service
boundary.
"""

from __future__ import annotations

from typing import Any

from micro_agent.definition.models import InputContract, OutputContract, ParameterDefinition


class ContractValidationError(ValueError):
    """Raised when an invocation value violates a declared contract."""

    def __init__(self, contract: str, errors: list[str]) -> None:
        self.contract = contract
        self.errors = errors
        super().__init__(f"Invalid {contract} contract: {'; '.join(errors)}")


def validate_input(contract: InputContract, value: Any) -> None:
    """Validate an invocation input object against an input contract."""
    _validate_contract(contract.parameters, value, "input")


def validate_output(contract: OutputContract, value: Any) -> None:
    """Validate a runtime output object against an output contract."""
    _validate_contract(contract.parameters, value, "output")


def _validate_contract(
    parameters: list[ParameterDefinition], value: Any, contract_name: str
) -> None:
    # An empty contract is intentionally unconstrained for definitions that
    # have not declared a schema yet.
    if not parameters:
        return

    errors: list[str] = []
    if not isinstance(value, dict):
        raise ContractValidationError(contract_name, ["value must be a JSON object"])

    declared = {parameter.name: parameter for parameter in parameters}
    for parameter in parameters:
        if parameter.required and parameter.name not in value:
            errors.append(f"missing required field '{parameter.name}'")
            continue
        if parameter.name in value and not _matches_type(value[parameter.name], parameter.type):
            errors.append(
                f"field '{parameter.name}' must be {parameter.type}, "
                f"got {type(value[parameter.name]).__name__}"
            )

    unknown = sorted(set(value) - set(declared))
    if unknown:
        errors.append(f"unknown field(s): {', '.join(unknown)}")

    if errors:
        raise ContractValidationError(contract_name, errors)


def _matches_type(value: Any, type_name: str) -> bool:
    """Match the JSON-compatible primitive types supported by v1alpha1."""
    if type_name == "any":
        return True
    if type_name == "null":
        return value is None
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    return False
