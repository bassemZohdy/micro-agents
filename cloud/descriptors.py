"""Versioned agent and skill descriptors for Micro-Agent Cloud (C1).

A descriptor is the semantic half of discovery (see
``docs/architecture/CLOUD_ARCHITECTURE.md``): what an agent is, what it can
do, and who may see it. Descriptors are derived from the agent's own
``MicroAgentDefinition`` and its served A2A agent card, never hand-written
facts about a live agent — the builder refuses a card that contradicts the
definition it claims to describe.

This module lives in the ``cloud`` package, which may import the core; the
core never imports cloud code (ADR 0013).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

DESCRIPTOR_SCHEMA_VERSION = "v1alpha1"

_DEFAULT_A2A_PROTOCOL_VERSION = "0.3.0"


class DescriptorError(ValueError):
    """Raised when a descriptor is invalid or contradicts its agent card."""


class DescriptorCardMismatchError(DescriptorError):
    """Raised when a served agent card contradicts the definition-derived descriptor."""


@dataclass
class SkillDescriptor:
    """One declared skill, carried verbatim from the definition."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class AgentDescriptor:
    """Semantic facts about one agent version, fit for registry storage."""

    schema_version: str = DESCRIPTOR_SCHEMA_VERSION
    name: str = ""
    version: str = ""
    description: str = ""
    a2a_protocol_version: str = _DEFAULT_A2A_PROTOCOL_VERSION
    card_url: str = ""
    card_fingerprint: str = ""
    skills: list[SkillDescriptor] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # Tenants that may discover this agent; empty means unrestricted.
    visibility: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentDescriptor:
        version = str(payload.get("schema_version", ""))
        if version != DESCRIPTOR_SCHEMA_VERSION:
            raise DescriptorError(
                f"unsupported descriptor schema version '{version}'; "
                f"supported: {DESCRIPTOR_SCHEMA_VERSION}"
            )
        skills = [
            SkillDescriptor(
                id=str(skill["id"]),
                name=str(skill["name"]),
                description=str(skill.get("description", "")),
                tags=[str(tag) for tag in skill.get("tags", [])],
            )
            for skill in payload.get("skills", [])
        ]
        return cls(
            schema_version=version,
            name=str(payload.get("name", "")),
            version=str(payload.get("version", "")),
            description=str(payload.get("description", "")),
            a2a_protocol_version=str(payload.get("a2a_protocol_version", "")),
            card_url=str(payload.get("card_url", "")),
            card_fingerprint=str(payload.get("card_fingerprint", "")),
            skills=skills,
            capabilities={str(k): bool(v) for k, v in payload.get("capabilities", {}).items()},
            labels={str(k): str(v) for k, v in payload.get("labels", {}).items()},
            visibility=[str(t) for t in payload.get("visibility", [])],
        )


def card_fingerprint(card: dict[str, Any]) -> str:
    """Stable hash of a served agent card's semantic content."""
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def descriptor_from_definition(
    definition: Any,
    *,
    card_url: str,
    card: dict[str, Any] | None = None,
) -> AgentDescriptor:
    """Derive a registry descriptor from a definition and its served card.

    ``card`` is the payload the agent serves at ``/.well-known/agent-card.json``.
    When present, the identity fields must agree with the definition — a
    registration whose card contradicts its descriptor is rejected here rather
    than trusted later.
    """
    dependencies = definition.spec.dependencies
    skills = [
        SkillDescriptor(
            id=skill.id,
            name=skill.name,
            description=skill.description or "",
            tags=list(skill.tags),
        )
        for skill in dependencies.skills
    ]
    capabilities = {
        "memory": dependencies.memory is not None,
        "tools": bool(dependencies.tools),
        "mcp": bool(dependencies.mcp_servers),
        "knowledge": bool(dependencies.knowledge),
        "session": dependencies.session.persistence != "none",
    }
    interop = definition.spec.interoperability
    a2a_config = interop.a2a if interop is not None else None
    protocol = (
        str(a2a_config.protocol_version)
        if a2a_config is not None and a2a_config.protocol_version
        else _DEFAULT_A2A_PROTOCOL_VERSION
    )

    descriptor = AgentDescriptor(
        name=str(definition.metadata.name),
        version=str(definition.metadata.version),
        description=str(definition.metadata.description or ""),
        a2a_protocol_version=protocol,
        card_url=card_url,
        card_fingerprint=card_fingerprint(card) if card is not None else "",
        skills=skills,
        capabilities=capabilities,
        labels=dict(definition.metadata.labels),
    )
    if card is not None:
        _check_card_agreement(descriptor, card)
    return descriptor


def _check_card_agreement(descriptor: AgentDescriptor, card: dict[str, Any]) -> None:
    mismatches: list[str] = []
    card_name = str(card.get("name", ""))
    card_version = str(card.get("version", ""))
    card_protocol = str(card.get("protocol_version", ""))
    if card_name and card_name != descriptor.name:
        mismatches.append(f"name: card says '{card_name}', definition says '{descriptor.name}'")
    if card_version and card_version != descriptor.version:
        mismatches.append(
            f"version: card says '{card_version}', definition says '{descriptor.version}'"
        )
    if card_protocol and card_protocol != descriptor.a2a_protocol_version:
        mismatches.append(
            "a2a protocol version: card says "
            f"'{card_protocol}', definition says '{descriptor.a2a_protocol_version}'"
        )
    card_skills = card.get("skills") or []
    descriptor_skill_ids = {skill.id for skill in descriptor.skills}
    for skill in card_skills:
        skill_id = str(skill.get("id", ""))
        if skill_id and skill_id not in descriptor_skill_ids:
            mismatches.append(f"card advertises unknown skill '{skill_id}'")
    if mismatches:
        raise DescriptorCardMismatchError(
            "served agent card contradicts the definition: " + "; ".join(mismatches)
        )


__all__ = [
    "DESCRIPTOR_SCHEMA_VERSION",
    "AgentDescriptor",
    "DescriptorCardMismatchError",
    "DescriptorError",
    "SkillDescriptor",
    "card_fingerprint",
    "descriptor_from_definition",
]
