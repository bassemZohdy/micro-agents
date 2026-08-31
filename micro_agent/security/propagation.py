"""Invocation identity propagation.

Verified identity travels with the running invocation through a context
variable — the same mechanism OpenTelemetry uses for trace context — so
model, tool, and MCP operations (and any downstream code they call) read the
verified principal without every SPI signature carrying it. The runtime
binds the identity at invocation start from the authenticated request; it is
never derived from caller-supplied request metadata.

Workload identity is resolved once per process: environment overrides first,
then the Kubernetes service-account namespace mount, then the hostname as a
best-effort workload identifier.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path

from micro_agent.security.identity import CallerIdentity, RuntimeIdentity, UserContext

_K8S_SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


@dataclass(frozen=True)
class InvocationIdentity:
    """The verified principals bound to a running invocation."""

    caller: CallerIdentity | None = None
    user: UserContext | None = None
    workload: RuntimeIdentity | None = None


_current: ContextVar[InvocationIdentity | None] = ContextVar(
    "micro_agent_invocation_identity", default=None
)


def get_invocation_identity() -> InvocationIdentity | None:
    """The verified identity visible to the current operation, if any."""
    return _current.get()


def set_invocation_identity(
    identity: InvocationIdentity | None,
) -> Token[InvocationIdentity | None]:
    """Bind identity to the current context; returns a reset token."""
    return _current.set(identity)


def reset_invocation_identity(token: Token[InvocationIdentity | None]) -> None:
    """Restore the previous identity binding."""
    _current.reset(token)


@contextmanager
def invocation_identity(identity: InvocationIdentity | None) -> Iterator[None]:
    """Bind identity for the duration of the block."""
    token = set_invocation_identity(identity)
    try:
        yield
    finally:
        reset_invocation_identity(token)


def resolve_workload_identity(
    *,
    environ: dict[str, str] | None = None,
    hostname: str | None = None,
) -> RuntimeIdentity:
    """Resolve the process workload identity from the platform.

    Precedence: explicit environment overrides, then the Kubernetes
    service-account namespace mount (with the pod hostname as the workload
    id), then the hostname alone. Workloads outside Kubernetes resolve to a
    hostname-identified runtime identity so audit attribution always has a
    principal.
    """
    env = environ if environ is not None else dict(os.environ)
    workload_id = env.get("MICRO_AGENT_WORKLOAD_ID") or hostname or socket.gethostname()
    namespace = env.get("MICRO_AGENT_WORKLOAD_NAMESPACE")
    service_account = env.get("MICRO_AGENT_SERVICE_ACCOUNT")
    if namespace is None:
        try:
            namespace_file = Path(_K8S_SA_NAMESPACE_PATH)
            if namespace_file.exists():
                namespace = namespace_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            namespace = None
    return RuntimeIdentity(
        workload_id=workload_id,
        namespace=namespace or "default",
        service_account=service_account,
    )


__all__ = [
    "InvocationIdentity",
    "get_invocation_identity",
    "invocation_identity",
    "reset_invocation_identity",
    "resolve_workload_identity",
    "set_invocation_identity",
]
