"""Micro-Agent Health and Readiness.

Defines multiple health levels:
- Liveness
- Readiness
- Dependency Health
- Capability Health
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Health Status
# ---------------------------------------------------------------------------


class HealthStatus(StrEnum):
    """Health status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


# ---------------------------------------------------------------------------
# Health Check Results
# ---------------------------------------------------------------------------


@dataclass
class DependencyHealth:
    """Health of a single dependency."""

    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class LivenessResult:
    """Liveness check result — is the process alive?"""

    alive: bool = True
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadinessResult:
    """Readiness check result — is the agent ready to serve?"""

    ready: bool = True
    status: HealthStatus = HealthStatus.HEALTHY
    dependencies: list[DependencyHealth] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.ready and self.status != HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# Health Checker Interface
# ---------------------------------------------------------------------------


class HealthChecker:
    """Health checker for a Micro-Agent."""

    def __init__(self) -> None:
        self._dependencies: list[DependencyHealth] = []

    def add_dependency(self, name: str, status: HealthStatus = HealthStatus.HEALTHY) -> None:
        """Register a dependency health check."""
        self._dependencies.append(DependencyHealth(name=name, status=status))

    def check_liveness(self) -> LivenessResult:
        """Check if the process is alive."""
        return LivenessResult(alive=True)

    def check_readiness(self) -> ReadinessResult:
        """Check if the agent is ready to serve."""
        unhealthy = [d for d in self._dependencies if d.status == HealthStatus.UNHEALTHY]
        degraded = [d for d in self._dependencies if d.status == HealthStatus.DEGRADED]

        if unhealthy:
            return ReadinessResult(
                ready=False,
                status=HealthStatus.UNHEALTHY,
                dependencies=list(self._dependencies),
            )
        if degraded:
            return ReadinessResult(
                ready=True,
                status=HealthStatus.DEGRADED,
                dependencies=list(self._dependencies),
            )
        return ReadinessResult(
            ready=True,
            status=HealthStatus.HEALTHY,
            dependencies=list(self._dependencies),
        )
