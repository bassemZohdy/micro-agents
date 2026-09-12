"""Versioned JSON contracts for the Micro-Agent Cloud reference planes.

The cloud package deliberately keeps these contracts separate from the core
``MicroAgentDefinition`` schema.  Cloud payloads are control-plane records and
may evolve on their own compatibility track.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

CLOUD_SCHEMA_VERSION = "v1alpha1"
_SCHEMA_ROOT = Path(__file__).parent.parent / "docs" / "schemas"


class SkillDescriptorContract(BaseModel):
    """Wire contract for one discoverable agent skill."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class AgentDescriptorContract(BaseModel):
    """Versioned registry descriptor contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1alpha1"] = "v1alpha1"
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    a2a_protocol_version: str = Field(min_length=1)
    card_url: str = ""
    card_fingerprint: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    skills: list[SkillDescriptorContract] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    visibility: list[str] = Field(default_factory=list)


class ConfigRecordContract(BaseModel):
    """Versioned response envelope for a stored definition or overlay."""

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1)
    kind: Literal["definition", "overlay"]
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: float
    payload: dict[str, Any] | None = None


class ObservabilityEventContract(BaseModel):
    """One observability event; unknown fields remain additive-compatible."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["span", "usage", "audit"]
    trace_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    tenant: str | None = None
    span_id: str | None = None
    name: str | None = None
    parent_span_id: str | None = None
    caller_agent: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    status: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    action: str | None = None
    decision: str | None = None


class ObservabilityBatchContract(BaseModel):
    """Versioned observability ingestion envelope."""

    model_config = ConfigDict(extra="forbid")

    events: list[ObservabilityEventContract] = Field(max_length=1000)


CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "cloud-descriptor-v1alpha1": AgentDescriptorContract,
    "cloud-config-v1alpha1": ConfigRecordContract,
    "cloud-observability-v1alpha1": ObservabilityBatchContract,
}


def generate_schemas() -> dict[str, dict[str, Any]]:
    """Return the checked-in cloud JSON Schema documents."""
    schemas: dict[str, dict[str, Any]] = {}
    for name, model in CONTRACT_MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://microagents.io/schemas/cloud/{CLOUD_SCHEMA_VERSION}/{name}.json"
        schema["title"] = f"Micro-Agent Cloud {name} contract"
        schemas[name] = schema
    return schemas


def load_schema(name: str) -> dict[str, Any]:
    """Load one checked-in cloud schema by its stable document name."""
    if name not in CONTRACT_MODELS:
        raise ValueError(f"unsupported cloud schema: {name}")
    path = _SCHEMA_ROOT / f"{name}.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def write_schemas(directory: Path | str | None = None) -> list[Path]:
    """Write all cloud schemas, keeping generated artifacts reproducible."""
    root = Path(directory) if directory is not None else _SCHEMA_ROOT
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, schema in generate_schemas().items():
        path = root / f"{name}.json"
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    print("Schemas written to " + ", ".join(str(path) for path in write_schemas()))


__all__ = [
    "CLOUD_SCHEMA_VERSION",
    "CONTRACT_MODELS",
    "AgentDescriptorContract",
    "ConfigRecordContract",
    "ObservabilityBatchContract",
    "ObservabilityEventContract",
    "SkillDescriptorContract",
    "generate_schemas",
    "load_schema",
    "write_schemas",
]
