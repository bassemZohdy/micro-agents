"""Micro-Agent A2A interoperability via the official a2a-sdk.

The agent card is the SDK's model served at the standard
``/.well-known/agent-card.json`` route, and the JSON-RPC transport bridges
A2A tasks onto Micro-Agent invocations with non-streaming and streaming task
lifecycles when the bound runtime supports streaming. Declared protocol
versions must be supported by the installed SDK; requests declaring an
unsupported version are rejected.
"""

from __future__ import annotations

from typing import Any

from micro_agent.definition import A2AConfig

_A2A_WELL_KNOWN_PATH = "/.well-known/agent-card.json"
_DEFAULT_PROTOCOL_VERSION = "0.3.0"

SUPPORTED_PROTOCOL_VERSIONS = frozenset({"0.3.0"})


class A2aSdkUnavailableError(RuntimeError):
    """Raised when A2A features are requested without the official SDK."""

    def __init__(self) -> None:
        super().__init__(
            "the official a2a-sdk is required; install the optional 'a2a' "
            "extra ('micro-agents[a2a]')"
        )


class UnsupportedProtocolVersionError(RuntimeError):
    """Raised when a declared or requested A2A version is not supported."""


def a2a_well_known_path() -> str:
    """The standard A2A agent-card discovery path."""
    return _A2A_WELL_KNOWN_PATH


def _import_sdk() -> Any:
    try:
        import a2a.types as a2a_types
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise A2aSdkUnavailableError() from exc
    return a2a_types


def normalize_protocol_version(declared: str | None) -> str:
    """Validate a declared protocol version against the supported set."""
    version = (declared or _DEFAULT_PROTOCOL_VERSION).strip()
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_PROTOCOL_VERSIONS))
        raise UnsupportedProtocolVersionError(
            f"unsupported A2A protocol version '{declared}'; supported: {supported}"
        )
    return version


def skills_mapping(definition: Any) -> list[Any]:
    """Map a definition's skills to SDK AgentSkill models."""
    a2a_types = _import_sdk()
    return [
        a2a_types.AgentSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description or "",
            tags=list(skill.tags),
        )
        for skill in definition.spec.dependencies.skills
    ]


def agent_card_from_definition(
    definition: Any,
    base_url: str | None = None,
    security_scheme: dict[str, Any] | None = None,
    scheme_name: str = "oidc",
    streaming: bool = False,
) -> Any:
    """Build the SDK AgentCard from a MicroAgentDefinition.

    ``security_scheme`` is a concrete scheme payload (for example from
    :meth:`~micro_agent.security.auth.Authenticator.security_scheme`);
    when present the card advertises it as a requirement for interaction.
    """
    a2a_types = _import_sdk()
    a2a_config = definition.spec.interoperability.a2a if definition.spec.interoperability else None
    declared = a2a_config.protocol_version if a2a_config else None
    url = base_url or (a2a_config.endpoint if a2a_config else "") or ""
    security: list[dict[str, list[str]]] | None = None
    security_schemes: dict[str, Any] | None = None
    if security_scheme:
        security_schemes = {
            scheme_name: a2a_types.SecurityScheme(root=security_scheme),
        }
        security = [{scheme_name: []}]
    return a2a_types.AgentCard(
        name=definition.metadata.name,
        description=definition.metadata.description or "",
        version=definition.metadata.version,
        url=url,
        preferred_transport="JSONRPC",
        protocol_version=normalize_protocol_version(declared),
        skills=skills_mapping(definition),
        capabilities=a2a_types.AgentCapabilities(
            streaming=streaming,
            push_notifications=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        security=security,
        security_schemes=security_schemes,
    )


__all__ = [
    "A2AConfig",
    "A2aSdkUnavailableError",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UnsupportedProtocolVersionError",
    "a2a_well_known_path",
    "agent_card_from_definition",
    "normalize_protocol_version",
    "skills_mapping",
]
