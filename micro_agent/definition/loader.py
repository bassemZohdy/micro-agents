"""Micro-Agent Definition loader — YAML parsing and validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
from pydantic import ValidationError

from micro_agent.definition.models import MicroAgentDefinition

SUPPORTED_API_VERSIONS = frozenset({"microagents.io/v1alpha1", "microagents.io/v1beta1"})


_V1BETA1_FIELD_ALIASES = {
    "inputContract": "input_contract",
    "outputContract": "output_contract",
    "mcpServers": "mcp_servers",
    "inputMetadata": "input_metadata",
    "outputMetadata": "output_metadata",
    "allowedCapabilities": "allowed_capabilities",
    "sourceType": "source_type",
    "maxResults": "max_results",
    "maxContextCharacters": "max_context_characters",
    "timeoutSeconds": "timeout_seconds",
    "maxIterations": "max_iterations",
    "maxConcurrency": "max_concurrency",
    "shutdownTimeoutSeconds": "shutdown_timeout_seconds",
    "concurrencyPolicy": "concurrency_policy",
    "errorPolicy": "error_policy",
    "retryMaxAttempts": "retry_max_attempts",
    "retryBackoffSeconds": "retry_backoff_seconds",
    "retryJitterSeconds": "retry_jitter_seconds",
    "retryBudgetSeconds": "retry_budget_seconds",
    "circuitBreakerFailures": "circuit_breaker_failures",
    "circuitBreakerCooldownSeconds": "circuit_breaker_cooldown_seconds",
    "protocolVersion": "protocol_version",
    "identityRequirements": "identity_requirements",
    "credentialRefs": "credential_refs",
    "policyRefs": "policy_refs",
    "sideEffect": "side_effect",
    "credentialRef": "credential_ref",
    "modelId": "model_id",
    "modelProvider": "provider",
}


def _migrate_v1beta1(data: dict[str, object]) -> dict[str, object]:
    """Translate the beta camelCase wire spelling into the stable model."""
    migrated = deepcopy(data)

    def visit(value: object) -> object:
        if isinstance(value, dict):
            return {
                _V1BETA1_FIELD_ALIASES.get(str(key), key): visit(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    return visit(migrated)  # type: ignore[return-value]


class DefinitionError(Exception):
    """Raised when a definition is invalid."""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def load_definition_from_dict(data: dict[str, object]) -> MicroAgentDefinition:
    """Parse and validate a Micro-Agent definition from a dictionary.

    Raises DefinitionError with useful diagnostics on validation failure.
    """
    version = data.get("apiVersion", data.get("api_version", "microagents.io/v1alpha1"))
    if version not in SUPPORTED_API_VERSIONS:
        raise DefinitionError(
            f"Unsupported Micro-Agent API version: {version}",
            errors=[{"loc": "apiVersion", "msg": "unsupported API version", "type": "value_error"}],
        )
    normalized = _migrate_v1beta1(data) if version == "microagents.io/v1beta1" else data
    try:
        return MicroAgentDefinition.model_validate(normalized)
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
        summary = "; ".join(f"{error['loc']}: {error['msg']}" for error in errors)
        raise DefinitionError(
            f"Invalid Micro-Agent definition: {len(errors)} error(s): {summary}",
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


__all__ = [
    "DefinitionError",
    "SUPPORTED_API_VERSIONS",
    "_V1BETA1_FIELD_ALIASES",
    "load_definition_from_dict",
    "load_definition_from_file",
    "load_definition_from_yaml",
]
