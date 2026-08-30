"""Micro-Agent Definition v1alpha1 — typed Python models.

All types are runtime-neutral. No ADK-native or framework-native types appear here.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
_PARAMETER_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_TOKEN_RE = re.compile(_TOKEN_PATTERN)


def _validate_token(value: str, field_name: str) -> str:
    """Validate a portable identifier/reference and reject hidden whitespace."""
    if value != value.strip() or not _TOKEN_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, '.', '_', ':', '/', or '-' "
            "and must not contain whitespace"
        )
    return value


def _validate_url(value: str, field_name: str) -> str:
    """Require an absolute HTTP(S) URL with a host component."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute http(s) URL")
    return value


def _ensure_unique(values: list[str], field_name: str) -> None:
    """Reject duplicate names in ordered definition collections."""
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(
            f"{field_name} must be unique; duplicate value(s): {', '.join(duplicates)}"
        )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class ObjectMeta(BaseModel, extra="forbid"):
    """Standard object metadata."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=63,
        pattern=_NAME_PATTERN,
        description="Unique DNS-compatible agent name.",
    )
    version: str = Field(
        ...,
        min_length=5,
        max_length=128,
        pattern=_SEMVER_PATTERN,
        description="Semantic version of the agent.",
    )
    description: str | None = Field(None, description="Human-readable description.")
    labels: dict[str, str] = Field(default_factory=dict, description="Key-value labels.")
    annotations: dict[str, str] = Field(default_factory=dict, description="Key-value annotations.")


# ---------------------------------------------------------------------------
# Agent behavior
# ---------------------------------------------------------------------------


class ParameterDefinition(BaseModel, extra="forbid"):
    """Defines a single parameter in an input/output contract."""

    name: str = Field(..., min_length=1, max_length=128, pattern=_PARAMETER_PATTERN)
    type: str = Field(
        ...,
        min_length=1,
        pattern=r"^(string|integer|number|boolean|object|array|null|any)$",
        description="Parameter type (e.g. string, integer, boolean, object, array).",
    )
    description: str | None = None
    required: bool = True
    default: Any = None


class InputContract(BaseModel, extra="forbid"):
    """Describes the expected input for the agent."""

    parameters: list[ParameterDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_parameters(self) -> InputContract:
        _ensure_unique([parameter.name for parameter in self.parameters], "input parameters")
        return self


class OutputContract(BaseModel, extra="forbid"):
    """Describes the expected output from the agent."""

    parameters: list[ParameterDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_parameters(self) -> OutputContract:
        _ensure_unique([parameter.name for parameter in self.parameters], "output parameters")
        return self


class AgentBehavior(BaseModel, extra="forbid"):
    """Defines agent behavioral instructions and contracts."""

    instructions: str = Field(..., min_length=1, description="System instructions for the agent.")
    input_contract: InputContract = Field(default_factory=InputContract)
    output_contract: OutputContract = Field(default_factory=OutputContract)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class ModelRef(BaseModel, extra="forbid"):
    """Reference to a model provider."""

    ref: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_TOKEN_PATTERN,
        description="Model reference identifier.",
    )
    model_id: str | None = Field(
        None,
        min_length=1,
        max_length=256,
        description="Provider-specific model identifier.",
    )
    provider: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=_TOKEN_PATTERN,
        description="Model provider name.",
    )
    endpoint: str | None = Field(None, description="Model endpoint URL.")
    credential_ref: str | None = Field(None, description="Reference to external credential.")
    generation: dict[str, Any] = Field(
        default_factory=dict, description="Generation configuration."
    )
    timeout_seconds: int | None = Field(None, ge=1, description="Model call timeout.")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_url(value, "model endpoint")
        return value

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_token(value, "model credential reference")
        return value


class ToolDefinition(BaseModel, extra="forbid"):
    """Defines a tool available to the agent."""

    name: str = Field(..., min_length=1, max_length=128, pattern=_TOKEN_PATTERN)
    description: str | None = None
    source: str | None = Field(
        None,
        pattern=r"^(native|mcp|openapi)$",
        description="Tool source (native, mcp, openapi).",
    )
    timeout_seconds: int | None = Field(None, ge=1)


class McpServerRef(BaseModel, extra="forbid"):
    """Reference to an MCP server."""

    ref: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_TOKEN_PATTERN,
        description="MCP server reference identifier.",
    )
    transport: str | None = Field(
        None,
        pattern=r"^(stdio|sse|streamable-http)$",
        description="Transport type (stdio, sse, streamable-http).",
    )
    endpoint: str | None = Field(None, description="MCP server endpoint URL.")
    credential_ref: str | None = Field(None, description="Reference to external credential.")
    allowed_capabilities: list[str] = Field(
        default_factory=list, description="Allowed MCP capabilities."
    )
    timeout_seconds: int | None = Field(None, ge=1)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_url(value, "MCP endpoint")
        return value

    @model_validator(mode="after")
    def validate_transport_binding(self) -> McpServerRef:
        if self.transport in {"sse", "streamable-http"} and self.endpoint is None:
            raise ValueError(f"MCP endpoint is required for {self.transport} transport")
        if self.transport == "stdio" and self.endpoint is not None:
            raise ValueError("MCP endpoint must be omitted for stdio transport")
        return self

    @field_validator("credential_ref")
    @classmethod
    def validate_credential_ref(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_token(value, "MCP credential reference")
        return value

    @field_validator("allowed_capabilities")
    @classmethod
    def validate_allowed_capabilities(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_token(value, "MCP capability")
        _ensure_unique(values, "MCP capabilities")
        return values


class SkillDefinition(BaseModel, extra="forbid"):
    """Defines a semantic skill/capability."""

    id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_TOKEN_PATTERN,
        description="Unique skill identifier.",
    )
    name: str = Field(..., min_length=1, description="Human-readable skill name.")
    description: str | None = None
    input_metadata: dict[str, Any] = Field(default_factory=dict)
    output_metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_token(value, "skill tag")
        _ensure_unique(values, "skill tags")
        return values


class KnowledgeRef(BaseModel, extra="forbid"):
    """Reference to an external knowledge source."""

    ref: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_TOKEN_PATTERN,
        description="Knowledge source reference identifier.",
    )
    source_type: str | None = Field(None, description="Type of knowledge source.")
    version: str | None = None


class MemoryRef(BaseModel, extra="forbid"):
    """Reference to a memory provider."""

    ref: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_TOKEN_PATTERN,
        description="Memory provider reference identifier.",
    )
    scope: str | None = Field(
        None,
        pattern=r"^(user|agent|tenant|domain|application)$",
        description="Memory scope (user, agent, tenant, domain, application).",
    )


class SessionConfig(BaseModel, extra="forbid"):
    """Session configuration."""

    persistence: str = Field(
        "none",
        pattern=r"^(none|memory|sqlite|external)$",
        description="Session persistence mode (none, memory, sqlite, external).",
    )
    ttl_seconds: int | None = Field(None, ge=1, description="Session time-to-live.")


class Dependencies(BaseModel, extra="forbid"):
    """All agent dependencies."""

    model: ModelRef | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    mcp_servers: list[McpServerRef] = Field(default_factory=list)
    skills: list[SkillDefinition] = Field(default_factory=list)
    knowledge: list[KnowledgeRef] = Field(default_factory=list)
    memory: MemoryRef | None = None
    session: SessionConfig = Field(default_factory=SessionConfig)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def validate_unique_dependencies(self) -> Dependencies:
        _ensure_unique([tool.name for tool in self.tools], "tool names")
        _ensure_unique([server.ref for server in self.mcp_servers], "MCP server references")
        _ensure_unique([skill.id for skill in self.skills], "skill IDs")
        _ensure_unique([knowledge.ref for knowledge in self.knowledge], "knowledge references")
        return self


# ---------------------------------------------------------------------------
# Runtime semantics
# ---------------------------------------------------------------------------


class ErrorPolicy(StrEnum):
    """Error handling policy."""

    FAIL = "fail"
    RETRY = "retry"
    FALLBACK = "fallback"


class ConcurrencyPolicy(StrEnum):
    """Behavior when the invocation concurrency limit is reached."""

    WAIT = "wait"
    REJECT = "reject"


class RuntimeSemantics(BaseModel, extra="forbid"):
    """Runtime behavior configuration."""

    timeout_seconds: int | None = Field(None, ge=1, description="Overall invocation timeout.")
    max_iterations: int | None = Field(None, ge=1, description="Maximum agent iterations.")
    max_concurrency: int | None = Field(
        None, ge=1, description="Maximum concurrent invocations for this agent."
    )
    shutdown_timeout_seconds: float | None = Field(
        None,
        gt=0,
        description="Maximum time to drain invocations during shutdown.",
    )
    concurrency_policy: ConcurrencyPolicy = Field(
        ConcurrencyPolicy.WAIT,
        description="Whether invocations wait for capacity or are rejected.",
    )
    error_policy: ErrorPolicy = Field(ErrorPolicy.FAIL, description="Error handling policy.")
    capabilities: list[str] = Field(
        default_factory=list, description="Declared runtime capabilities."
    )

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_token(value, "runtime capability")
        _ensure_unique(values, "runtime capabilities")
        return values


# ---------------------------------------------------------------------------
# Interoperability
# ---------------------------------------------------------------------------


class A2AConfig(BaseModel, extra="forbid"):
    """Agent-to-Agent interoperability configuration."""

    enabled: bool = False
    endpoint: str | None = None
    protocol_version: str | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            return _validate_url(value, "A2A endpoint")
        return value

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
            raise ValueError("A2A protocol_version must use a numeric major.minor[.patch] format")
        return value


class Interoperability(BaseModel, extra="forbid"):
    """Interoperability configuration."""

    a2a: A2AConfig = Field(default_factory=A2AConfig)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class Security(BaseModel, extra="forbid"):
    """Security configuration."""

    credential_refs: list[str] = Field(
        default_factory=list, description="References to external credentials."
    )
    identity_requirements: dict[str, Any] = Field(default_factory=dict)
    policy_refs: list[str] = Field(
        default_factory=list, description="References to policy definitions."
    )

    @field_validator("credential_refs", "policy_refs")
    @classmethod
    def validate_references(cls, values: list[str], info: Any) -> list[str]:
        field_name = str(info.field_name).replace("_", " ")
        for value in values:
            _validate_token(value, field_name)
        _ensure_unique(values, field_name)
        return values


# ---------------------------------------------------------------------------
# Top-level definition
# ---------------------------------------------------------------------------


class MicroAgentSpec(BaseModel, extra="forbid"):
    """The specification of a Micro-Agent."""

    behavior: AgentBehavior
    dependencies: Dependencies = Field(default_factory=Dependencies)
    runtime: RuntimeSemantics = Field(default_factory=RuntimeSemantics)  # type: ignore[arg-type]
    interoperability: Interoperability = Field(default_factory=Interoperability)
    security: Security = Field(default_factory=Security)


class MicroAgentDefinition(BaseModel, extra="forbid"):
    """Micro-Agent Definition v1alpha1.

    This is the top-level declarative definition of a Micro-Agent.
    It contains no ADK-native or framework-native types.
    """

    model_config = ConfigDict(populate_by_name=True)

    api_version: str = Field(
        "microagents.io/v1alpha1",
        alias="apiVersion",
        description="API version of this definition.",
        pattern=r"^microagents\.io/v1alpha1$",
    )
    kind: str = Field(
        "MicroAgent",
        description="Resource kind.",
        pattern=r"^MicroAgent$",
    )
    metadata: ObjectMeta
    spec: MicroAgentSpec
