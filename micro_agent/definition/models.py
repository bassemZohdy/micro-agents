"""Micro-Agent Definition v1alpha1 — typed Python models.

All types are runtime-neutral. No ADK-native or framework-native types appear here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class ObjectMeta(BaseModel, extra="forbid"):
    """Standard object metadata."""

    name: str = Field(..., min_length=1, description="Unique agent name.")
    version: str = Field(..., min_length=1, description="Semantic version of the agent.")
    description: str | None = Field(None, description="Human-readable description.")
    labels: dict[str, str] = Field(default_factory=dict, description="Key-value labels.")
    annotations: dict[str, str] = Field(default_factory=dict, description="Key-value annotations.")


# ---------------------------------------------------------------------------
# Agent behavior
# ---------------------------------------------------------------------------


class ParameterDefinition(BaseModel, extra="forbid"):
    """Defines a single parameter in an input/output contract."""

    name: str = Field(..., min_length=1)
    type: str = Field(
        ...,
        min_length=1,
        description="Parameter type (e.g. string, integer, boolean, object, array).",
    )
    description: str | None = None
    required: bool = True
    default: Any = None


class InputContract(BaseModel, extra="forbid"):
    """Describes the expected input for the agent."""

    parameters: list[ParameterDefinition] = Field(default_factory=list)


class OutputContract(BaseModel, extra="forbid"):
    """Describes the expected output from the agent."""

    parameters: list[ParameterDefinition] = Field(default_factory=list)


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

    ref: str = Field(..., min_length=1, description="Model reference identifier.")
    provider: str | None = Field(None, description="Model provider name.")
    endpoint: str | None = Field(None, description="Model endpoint URL.")
    credential_ref: str | None = Field(None, description="Reference to external credential.")
    generation: dict[str, Any] = Field(
        default_factory=dict, description="Generation configuration."
    )
    timeout_seconds: int | None = Field(None, ge=1, description="Model call timeout.")


class ToolDefinition(BaseModel, extra="forbid"):
    """Defines a tool available to the agent."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    source: str | None = Field(None, description="Tool source (native, mcp, openapi).")
    timeout_seconds: int | None = Field(None, ge=1)


class McpServerRef(BaseModel, extra="forbid"):
    """Reference to an MCP server."""

    ref: str = Field(..., min_length=1, description="MCP server reference identifier.")
    transport: str | None = Field(None, description="Transport type (stdio, sse, streamable-http).")
    endpoint: str | None = Field(None, description="MCP server endpoint URL.")
    credential_ref: str | None = Field(None, description="Reference to external credential.")
    allowed_capabilities: list[str] = Field(
        default_factory=list, description="Allowed MCP capabilities."
    )
    timeout_seconds: int | None = Field(None, ge=1)


class SkillDefinition(BaseModel, extra="forbid"):
    """Defines a semantic skill/capability."""

    id: str = Field(..., min_length=1, description="Unique skill identifier.")
    name: str = Field(..., min_length=1, description="Human-readable skill name.")
    description: str | None = None
    input_metadata: dict[str, Any] = Field(default_factory=dict)
    output_metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class KnowledgeRef(BaseModel, extra="forbid"):
    """Reference to an external knowledge source."""

    ref: str = Field(..., min_length=1, description="Knowledge source reference identifier.")
    source_type: str | None = Field(None, description="Type of knowledge source.")
    version: str | None = None


class MemoryRef(BaseModel, extra="forbid"):
    """Reference to a memory provider."""

    ref: str = Field(..., min_length=1, description="Memory provider reference identifier.")
    scope: str | None = Field(
        None,
        description="Memory scope (user, agent, tenant, domain, application).",
    )


class SessionConfig(BaseModel, extra="forbid"):
    """Session configuration."""

    persistence: str = Field("none", description="Session persistence mode (none, external).")
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
    concurrency_policy: ConcurrencyPolicy = Field(
        ConcurrencyPolicy.WAIT,
        description="Whether invocations wait for capacity or are rejected.",
    )
    error_policy: ErrorPolicy = Field(ErrorPolicy.FAIL, description="Error handling policy.")
    capabilities: list[str] = Field(
        default_factory=list, description="Declared runtime capabilities."
    )


# ---------------------------------------------------------------------------
# Interoperability
# ---------------------------------------------------------------------------


class A2AConfig(BaseModel, extra="forbid"):
    """Agent-to-Agent interoperability configuration."""

    enabled: bool = False
    endpoint: str | None = None
    protocol_version: str | None = None


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
