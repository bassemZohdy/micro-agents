"""Micro-Agent Skills and Capability Contract.

Skills represent externally advertised semantic capabilities.
A skill is not necessarily equivalent to one tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Skill Definition
# ---------------------------------------------------------------------------


@dataclass
class SkillDefinition:
    """A semantic skill/capability exposed by a Micro-Agent."""

    id: str
    name: str
    description: str | None = None
    input_metadata: dict[str, Any] = field(default_factory=dict)
    output_metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Capability Contract
# ---------------------------------------------------------------------------


@dataclass
class CapabilityContract:
    """The complete capability contract of a Micro-Agent.

    Distinguishes skills (semantic capabilities) from tools (implementation).
    """

    skills: list[SkillDefinition] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)

    def has_skill(self, skill_id: str) -> bool:
        """Check if the agent exposes a specific skill."""
        return any(s.id == skill_id for s in self.skills)

    def find_skill(self, skill_id: str) -> SkillDefinition | None:
        """Find a skill by ID."""
        for skill in self.skills:
            if skill.id == skill_id:
                return skill
        return None

    def skills_by_tag(self, tag: str) -> list[SkillDefinition]:
        """Find all skills with a specific tag."""
        return [s for s in self.skills if tag in s.tags]
