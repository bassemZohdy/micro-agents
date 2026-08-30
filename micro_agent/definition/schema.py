"""Generate JSON Schema from Micro-Agent Definition models."""

from __future__ import annotations

import json
from pathlib import Path

from micro_agent.definition.models import MicroAgentDefinition


def generate_schema() -> dict[str, object]:
    """Generate JSON Schema for MicroAgentDefinition."""
    schema = MicroAgentDefinition.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://microagents.io/schemas/v1alpha1/micro-agent.json"
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


if __name__ == "__main__":
    out = write_schema()
    print(f"Schema written to {out}")
