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


@dataclass
class A2AConfig:
    """A2A configuration."""

    enabled: bool = False
    endpoint: str | None = None
    protocol_version: str = "1.0"
    security: dict[str, Any] = field(default_factory=dict)
