"""Micro-Agent Definition loader — YAML parsing and validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from micro_agent.definition.models import MicroAgentDefinition


class DefinitionError(Exception):
    """Raised when a definition is invalid."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def load_definition_from_dict(data: dict[str, object]) -> MicroAgentDefinition:
    """Parse and validate a Micro-Agent definition from a dictionary.

    Raises DefinitionError with useful diagnostics on validation failure.
    """
    try:
        return MicroAgentDefinition.model_validate(data)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            errors.append(
                {
                    "loc": " -> ".join(str(item) for item in err["loc"]),
                    "msg": err["msg"],
                    "type": err["type"],
                }
            )
        raise DefinitionError(
            f"Invalid Micro-Agent definition: {len(errors)} error(s)",
            errors=errors,
        ) from exc


def load_definition_from_yaml(yaml_content: str) -> MicroAgentDefinition:
    """Parse and validate a Micro-Agent definition from a YAML string."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise DefinitionError(f"Invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise DefinitionError("YAML content must produce a mapping at the top level.")

    return load_definition_from_dict(data)


def load_definition_from_file(path: Path | str) -> MicroAgentDefinition:
    """Load and validate a Micro-Agent definition from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise DefinitionError(f"Definition file not found: {path}")
    if path.is_dir():
        raise DefinitionError(f"Definition path is a directory, not a file: {path}")
    content = path.read_text(encoding="utf-8")
    return load_definition_from_yaml(content)
