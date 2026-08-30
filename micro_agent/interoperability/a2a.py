"""Micro-Agent A2A (Agent-to-Agent) interoperability.

Uses existing A2A protocol for agent-to-agent communication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


@dataclass
class AgentSkill:
    """A skill exposed via A2A Agent Card."""

    id: str
    name: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class AgentCard:
    """A2A Agent Card for agent discovery."""

    name: str
    description: str = ""
    version: str = ""
    url: str = ""
    skills: list[AgentSkill] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# A2A Message
# ---------------------------------------------------------------------------


@dataclass
class A2AMessage:
    """An A2A protocol message."""

    role: str = "user"
    parts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class A2ATask:
    """An A2A task."""

    task_id: str = ""
    messages: list[A2AMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class A2AResponse:
    """An A2A task response."""

    task_id: str = ""
    status: str = "completed"
    messages: list[A2AMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# A2A Configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A2A Configuration
# ---------------------------------------------------------------------------

# The canonical A2AConfig is the pydantic model in
# micro_agent.definition.models; re-exported here so A2A consumers have one
# import path without a duplicate type.
from micro_agent.definition import A2AConfig  # noqa: E402

__all__ = [
    "A2AConfig",
    "A2AMessage",
    "A2AResponse",
    "A2ATask",
    "AgentCard",
    "AgentSkill",
    "a2a_well_known_path",
    "agent_card_from_definition",
    "skills_mapping",
]


# ---------------------------------------------------------------------------
# Agent Card generation (definition -> A2A)
# ---------------------------------------------------------------------------

_A2A_WELL_KNOWN_PATH = "/.well-known/agent.json"


def skills_mapping(definition: Any) -> list[AgentSkill]:
    """Map a definition's skills to A2A AgentCard skills.

    Conversion helper between the definition's SkillDefinition (pydantic) and
    the A2A AgentSkill shape (dataclass).
    """
    return [
        AgentSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=list(skill.tags),
        )
        for skill in definition.spec.dependencies.skills
    ]


def agent_card_from_definition(
    definition: Any,
    base_url: str | None = None,
    capabilities: dict[str, bool] | None = None,
) -> AgentCard:
    """Generate an A2A AgentCard from a MicroAgentDefinition."""
    a2a_config = definition.spec.interoperability.a2a if definition.spec.interoperability else None
    url = base_url or (a2a_config.endpoint if a2a_config else "") or ""
    security: dict[str, Any] = {}
    identity_requirements = (
        definition.spec.security.identity_requirements if definition.spec.security else {}
    )
    if identity_requirements.get("require_caller_identity"):
        security = {"callerIdentityRequired": True}
    return AgentCard(
        name=definition.metadata.name,
        description=definition.metadata.description or "",
        version=definition.metadata.version,
        url=url,
        skills=skills_mapping(definition),
        capabilities=capabilities or {"streaming": False, "pushNotifications": False},
        security=security,
        metadata={
            "protocolVersion": a2a_config.protocol_version if a2a_config else "1.0",
            "labels": dict(definition.metadata.labels),
        },
    )


def a2a_well_known_path() -> str:
    """The A2A well-known agent-card endpoint path."""
    return _A2A_WELL_KNOWN_PATH
