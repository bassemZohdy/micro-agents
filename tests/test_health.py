"""Tests for Micro-Agent Health and Readiness."""

from micro_agent.observability import (
    DependencyHealth,
    HealthChecker,
    HealthStatus,
    LivenessResult,
    ReadinessResult,
)


class TestHealthStatus:
    """Test health status values."""

    def test_statuses(self):
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.DEGRADED == "degraded"
        assert HealthStatus.UNHEALTHY == "unhealthy"


class TestDependencyHealth:
    """Test dependency health."""

    def test_healthy_dependency(self):
        dep = DependencyHealth(name="model")
        assert dep.status == HealthStatus.HEALTHY

    def test_unhealthy_dependency(self):
        dep = DependencyHealth(name="mcp", status=HealthStatus.UNHEALTHY)
        assert dep.status == HealthStatus.UNHEALTHY


class TestLivenessResult:
    """Test liveness result."""

    def test_alive(self):
        result = LivenessResult()
        assert result.alive is True


class TestReadinessResult:
    """Test readiness result."""

    def test_ready(self):
        result = ReadinessResult()
        assert result.is_ready is True

    def test_not_ready_when_unhealthy(self):
        result = ReadinessResult(ready=False, status=HealthStatus.UNHEALTHY)
        assert result.is_ready is False


class TestHealthChecker:
    """Test health checker."""

    def test_liveness(self):
        checker = HealthChecker()
        result = checker.check_liveness()
        assert result.alive is True

    def test_readiness_no_dependencies(self):
        checker = HealthChecker()
        result = checker.check_readiness()
        assert result.is_ready is True
        assert result.status == HealthStatus.HEALTHY

    def test_readiness_with_healthy_deps(self):
        checker = HealthChecker()
        checker.add_dependency("model")
        checker.add_dependency("mcp")
        result = checker.check_readiness()
        assert result.is_ready is True
        assert len(result.dependencies) == 2

    def test_readiness_with_unhealthy_dep(self):
        checker = HealthChecker()
        checker.add_dependency("model")
        checker.add_dependency("mcp", status=HealthStatus.UNHEALTHY)
        result = checker.check_readiness()
        assert result.is_ready is False
        assert result.status == HealthStatus.UNHEALTHY

    def test_readiness_with_degraded_dep(self):
        checker = HealthChecker()
        checker.add_dependency("model")
        checker.add_dependency("mcp", status=HealthStatus.DEGRADED)
        result = checker.check_readiness()
        assert result.is_ready is True
        assert result.status == HealthStatus.DEGRADED
