"""Generate JSON Schema from Micro-Agent Definition models."""

from __future__ import annotations

import json
from pathlib import Path

from micro_agent.definition.loader import _V1BETA1_FIELD_ALIASES
from micro_agent.definition.models import MicroAgentDefinition


def _camel_case_schema(schema: dict[str, object]) -> None:
    """Rewrite model properties to the v1beta1 camelCase wire spelling."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = schema.get("required")
        for camel, snake in _V1BETA1_FIELD_ALIASES.items():
            if snake in properties:
                properties[camel] = properties.pop(snake)
                if isinstance(required, list) and snake in required:
                    required[required.index(snake)] = camel
    for value in schema.values():
        if isinstance(value, dict):
            _camel_case_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _camel_case_schema(item)


def generate_schema(version: str = "v1alpha1") -> dict[str, object]:
    """Generate JSON Schema for one supported Micro-Agent API version."""
    if version not in {"v1alpha1", "v1beta1"}:
        raise ValueError(f"unsupported schema version: {version}")
    schema = MicroAgentDefinition.model_json_schema(by_alias=True)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        api_version = properties.get("apiVersion")
        if isinstance(api_version, dict):
            api_version["default"] = f"microagents.io/{version}"
            api_version["pattern"] = rf"^microagents\.io/{version}$"
    if version == "v1beta1":
        _camel_case_schema(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://microagents.io/schemas/{version}/micro-agent.json"
    schema["title"] = f"MicroAgentDefinition {version}"
    return schema


def write_schema(path: Path | str | None = None) -> Path:
    """Write JSON Schema to file."""
    if path is None:
        path = (
            Path(__file__).parent.parent.parent / "docs" / "schemas" / "micro-agent-v1alpha1.json"
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = generate_schema()
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return path


def write_versioned_schemas(directory: Path | str | None = None) -> list[Path]:
    """Write schemas for both supported API versions."""
    root = (
        Path(directory)
        if directory is not None
        else Path(__file__).parent.parent.parent / "docs" / "schemas"
    )
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for version in ("v1alpha1", "v1beta1"):
        path = root / f"micro-agent-{version}.json"
        path.write_text(json.dumps(generate_schema(version), indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    outputs = write_versioned_schemas()
    print("Schemas written to " + ", ".join(str(path) for path in outputs))
