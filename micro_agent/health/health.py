"""Micro-Agent Health and Readiness.

Defines multiple health levels:
- Liveness
- Readiness
- Dependency Health
- Capability Health
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# A dependency probe returns a health status (or bool) asynchronously.
DependencyProbe = Callable[[], Awaitable["HealthStatus | bool"]]

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
    """Health checker for a Micro-Agent.

    Dependencies may register an active probe (an async callable returning a
    HealthStatus or bool). probe_readiness() executes all probes, updates the
    stored statuses, and returns the resulting readiness. check_readiness()
    stays synchronous and reports the last known statuses.
    """

    def __init__(self, liveness_probe: Callable[[], bool] | None = None) -> None:
        self._dependencies: list[DependencyHealth] = []
        self._probes: dict[str, DependencyProbe] = {}
        self._liveness_probe = liveness_probe
        self._alive = True

    def add_dependency(
        self,
        name: str,
        status: HealthStatus = HealthStatus.HEALTHY,
        probe: DependencyProbe | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Register a dependency health check, optionally with an active probe."""
        self._dependencies.append(DependencyHealth(name=name, status=status, details=details or {}))
        if probe is not None:
            self._probes[name] = probe

    def update_status(
        self,
        name: str,
        status: HealthStatus,
        details: dict[str, Any] | None = None,
    ) -> bool:
        """Update a registered dependency's status. Returns False if unknown."""
        for dep in self._dependencies:
            if dep.name == name:
                dep.status = status
                if details is not None:
                    dep.details = details
                return True
        return False

    def set_alive(self, alive: bool) -> None:
        """Mark the process as (no longer) alive."""
        self._alive = alive

    def check_liveness(self) -> LivenessResult:
        """Check if the process is alive."""
        if not self._alive:
            return LivenessResult(alive=False, details={"reason": "process marked dead"})
        if self._liveness_probe is not None and not self._liveness_probe():
            return LivenessResult(alive=False, details={"reason": "liveness probe failed"})
        return LivenessResult(alive=True)

    async def probe_readiness(self) -> ReadinessResult:
        """Run all active dependency probes, update statuses, report readiness."""
        for name, probe in self._probes.items():
            try:
                result = await probe()
            except Exception as exc:  # noqa: BLE001 — a probe failure is a status
                self.update_status(name, HealthStatus.UNHEALTHY, {"error": str(exc)})
                continue
            if isinstance(result, bool):
                status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
                self.update_status(name, status)
            else:
                self.update_status(name, result)
        return self.check_readiness()

    def check_readiness(self) -> ReadinessResult:
        """Check if the agent is ready to serve (last known dependency statuses)."""
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
