"""Micro-Agent Skills and Capability Contract.

Skills represent externally advertised semantic capabilities.
A skill is not necessarily equivalent to one tool.

The canonical SkillDefinition is the pydantic model in
:mod:`micro_agent.definition.models`; it is re-exported here so the skills
package stays the semantic-capability entry point without duplicating the
type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from micro_agent.definition import SkillDefinition

if TYPE_CHECKING:
    from micro_agent.definition import MicroAgentDefinition

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


def capability_contract_from_definition(
    definition: MicroAgentDefinition,
) -> CapabilityContract:
    """Build a CapabilityContract from a MicroAgentDefinition."""
    deps = definition.spec.dependencies
    return CapabilityContract(
        skills=list(deps.skills),
        tools=[tool.name for tool in deps.tools],
        mcp_servers=[server.ref for server in deps.mcp_servers],
    )


__all__ = [
    "CapabilityContract",
    "SkillDefinition",
    "capability_contract_from_definition",
]
