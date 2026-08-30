"""Load a SecurityContext (and policy) from a definition's security section.

Credential *values* are resolved on demand through a caller-supplied resolver
and never stored on the context — only references travel with the definition.
"""

from __future__ import annotations

from collections.abc import Callable

from micro_agent.core.agent import AgentIdentity
from micro_agent.definition import MicroAgentDefinition
from micro_agent.security.identity import SecurityContext


def build_security_context(
    definition: MicroAgentDefinition,
    credential_resolver: Callable[[str], str | None] | None = None,
) -> SecurityContext:
    """Build a SecurityContext from a definition's security section."""
    security = definition.spec.security
    policy_refs = list(security.policy_refs) if security else []
    credential_refs = list(security.credential_refs) if security else []
    return SecurityContext(
        agent_identity=AgentIdentity(
            agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
            agent_name=definition.metadata.name,
            agent_version=definition.metadata.version,
        ),
        policy_refs=policy_refs,
        credential_refs=credential_refs,
    )


def resolve_credential(
    credential_ref: str,
    resolver: Callable[[str], str | None] | None,
) -> str | None:
    """Resolve a credential reference to its value via the supplied resolver."""
    if resolver is None:
        return None
    return resolver(credential_ref)
