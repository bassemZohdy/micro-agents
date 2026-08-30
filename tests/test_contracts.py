"""Tests for runtime-neutral input and output contract enforcement."""

import pytest

from micro_agent.definition import (
    ContractValidationError,
    InputContract,
    OutputContract,
    validate_input,
    validate_output,
)


def _input_contract() -> InputContract:
    return InputContract.model_validate(
        {
            "parameters": [
                {"name": "user_id", "type": "string"},
                {"name": "attempts", "type": "integer", "required": False},
            ]
        }
    )


def test_input_contract_accepts_declared_types() -> None:
    validate_input(_input_contract(), {"user_id": "u-1", "attempts": 2})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "missing required"),
        ({"user_id": 42}, "must be string"),
        ({"user_id": "u-1", "unexpected": True}, "unknown field"),
    ],
)
def test_input_contract_rejects_invalid_values(value: dict[str, object], message: str) -> None:
    with pytest.raises(ContractValidationError, match=message):
        validate_input(_input_contract(), value)


def test_empty_output_contract_is_unconstrained() -> None:
    validate_output(OutputContract(), {"content": "ok", "extra": [1]})


def test_output_contract_rejects_wrong_shape() -> None:
    contract = OutputContract.model_validate(
        {"parameters": [{"name": "content", "type": "string"}]}
    )
    with pytest.raises(ContractValidationError, match="must be string"):
        validate_output(contract, {"content": ["not", "text"]})
