"""Resilience tests: retryable-error taxonomy, circuit breaking, crash/replay."""

from __future__ import annotations

import asyncio

import pytest

from micro_agent.definition import load_definition_from_dict
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
)
from micro_agent.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    Retryability,
    classify_retry,
)
from micro_agent.security import OperationRegistry
from micro_agent.session import InMemorySessionProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


def _definition(**runtime_overrides) -> object:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "resilience-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "tools": [{"name": "echo", "source": "native"}],
                },
                "runtime": runtime_overrides,
            },
        }
    )


class TestRetryTaxonomy:
    """The explicit retryable-error classification."""

    def test_deterministic_errors_are_non_retryable(self):
        from micro_agent.core import AuthenticationError, ContinuationNotFoundError

        assert classify_retry(PermissionError("denied")) is Retryability.NON_RETRYABLE
        assert classify_retry(ValueError("bad")) is Retryability.NON_RETRYABLE
        assert classify_retry(AuthenticationError("no")) is Retryability.NON_RETRYABLE
        assert classify_retry(ContinuationNotFoundError("gone")) is Retryability.NON_RETRYABLE
        assert classify_retry(TimeoutError("late")) is Retryability.NON_RETRYABLE

    def test_transport_errors_are_retryable(self):
        from micro_agent.core import DependencyUnavailableError

        assert classify_retry(ConnectionError("reset")) is Retryability.RETRYABLE
        assert classify_retry(OSError("network")) is Retryability.RETRYABLE
        assert classify_retry(DependencyUnavailableError("down")) is Retryability.RETRYABLE

    def test_unknown_errors_keep_historical_retry_behavior(self):
        assert classify_retry(RuntimeError("mystery")) is Retryability.RETRYABLE

    def test_explicit_retryable_attribute_wins(self):
        class MarkedError(Exception):
            retryable = False

        class RetriableMarkedError(Exception):
            retryable = True

        assert classify_retry(MarkedError("x")) is Retryability.NON_RETRYABLE
        assert classify_retry(RetriableMarkedError("x")) is Retryability.RETRYABLE


class TestCircuitBreaker:
    """Closed → open → half-open → closed/reopened transitions."""

    def test_trips_after_threshold_and_rejects(self):
        clock = [100.0]
        breaker = CircuitBreaker(2, 5.0, clock=lambda: clock[0])
        assert breaker.record_failure() != "open"
        assert breaker.record_failure() == "open"
        with pytest.raises(CircuitOpenError, match="consecutive failures"):
            breaker.check(agent="a")

    def test_cooldown_elapses_to_half_open(self):
        clock = [100.0]
        breaker = CircuitBreaker(1, 5.0, clock=lambda: clock[0])
        breaker.record_failure()
        clock[0] += 6.0
        assert breaker.state == "half_open"
        breaker.check()  # probe allowed

    def test_probe_success_closes(self):
        clock = [100.0]
        breaker = CircuitBreaker(1, 5.0, clock=lambda: clock[0])
        breaker.record_failure()
        clock[0] += 6.0
        breaker.check()
        breaker.record_success()
        assert breaker.state == "closed"
        breaker.check()

    def test_probe_failure_reopens(self):
        clock = [100.0]
        breaker = CircuitBreaker(1, 5.0, clock=lambda: clock[0])
        breaker.record_failure()
        clock[0] += 6.0
        breaker.check()
        breaker.record_failure()
        assert breaker.state == "open"
        clock[0] += 2.0
        with pytest.raises(CircuitOpenError):
            breaker.check()


class TestRuntimeTaxonomy:
    """Bounded retries honor the classification."""

    def _runtime(self, provider) -> AdkRuntime:
        return AdkRuntime(AdkRuntimeConfig(model_provider=provider))

    @pytest.mark.asyncio
    async def test_deterministic_failure_fails_without_retries(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent
        from micro_agent.observability import Telemetry

        class DenyingProvider(FakeModelProvider):
            def __init__(self, config):
                super().__init__(config)
                self.calls = 0

            async def generate(self, config, messages, tools=None):
                self.calls += 1
                raise PermissionError("denied by policy")

        telemetry = Telemetry()
        provider = DenyingProvider(FakeModelConfig())
        runtime = self._runtime(provider)
        runtime._telemetry = telemetry
        agent = DefaultMicroAgent(_definition(error_policy="retry", retry_max_attempts=3), runtime)
        try:
            await agent.initialize()
            await agent.start()
            with pytest.raises(PermissionError):
                await agent.invoke(AgentRequest(input={}))
            assert provider.calls == 1, "deterministic errors must not retry"
            assert telemetry.metrics.get_metrics("agent_retries_total") == []
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried_within_bounds(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent
        from micro_agent.observability import Telemetry

        class FlakyProvider(FakeModelProvider):
            def __init__(self, config):
                super().__init__(config)
                self.calls = 0

            async def generate(self, config, messages, tools=None):
                self.calls += 1
                if self.calls < 3:
                    raise ConnectionError("reset by peer")
                return await super().generate(config, messages, tools=tools)

        telemetry = Telemetry()
        provider = FlakyProvider(FakeModelConfig(response="recovered"))
        runtime = self._runtime(provider)
        runtime._telemetry = telemetry
        agent = DefaultMicroAgent(
            _definition(error_policy="retry", retry_max_attempts=3, retry_backoff_seconds=0),
            runtime,
        )
        try:
            await agent.initialize()
            await agent.start()
            response = await agent.invoke(AgentRequest(input={}))
            assert response.status == "success"
            assert provider.calls == 3
            assert telemetry.metrics.get_metrics("agent_retries_total")
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()


class TestRuntimeCircuit:
    """Consecutive failures open the circuit; the probe closes or reopens."""

    @pytest.mark.asyncio
    async def test_circuit_opens_and_probe_closes(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent
        from micro_agent.observability import Telemetry

        class RecoveringProvider(FakeModelProvider):
            def __init__(self, config):
                super().__init__(config)
                self.calls = 0

            async def generate(self, config, messages, tools=None):
                self.calls += 1
                if self.calls <= 2:
                    raise ConnectionError("dependency down")
                return await super().generate(config, messages, tools=tools)

        telemetry = Telemetry()
        provider = RecoveringProvider(FakeModelConfig(response="back"))
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        runtime._telemetry = telemetry
        agent = DefaultMicroAgent(
            _definition(
                circuit_breaker_failures=2,
                circuit_breaker_cooldown_seconds=0.2,
            ),
            runtime,
        )
        try:
            await agent.initialize()
            await agent.start()
            for _ in range(2):
                with pytest.raises(ConnectionError):
                    await agent.invoke(AgentRequest(input={}))
            # Circuit open: the provider is not called at all.
            calls_after_trip = provider.calls
            with pytest.raises(CircuitOpenError):
                await agent.invoke(AgentRequest(input={}))
            assert provider.calls == calls_after_trip

            await asyncio.sleep(0.25)  # cooldown elapses → probe
            response = await agent.invoke(AgentRequest(input={}))
            assert response.status == "success"
            assert runtime._circuits["resilience-agent"].state == "closed"
            assert telemetry.metrics.get_metrics("circuit_breaker_trips_total")
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()


class TestCrashReplay:
    """A crash after a side effect must not re-execute it on replay."""

    @pytest.mark.asyncio
    async def test_replay_after_crash_deduplicates_executed_tool(self):
        from micro_agent.core import AgentRequest, DefaultMicroAgent

        executions: list[int] = []
        registry = OperationRegistry()
        session_provider = InMemorySessionProvider(ttl_seconds=300)

        class CrashProvider(FakeModelProvider):
            """Executes the tool, then crashes the invocation."""

            async def generate(self, config, messages, tools=None):
                if "assistant" in [m.get("role") for m in messages]:
                    raise ConnectionError("process crashed mid-invocation")
                return await super().generate(config, messages, tools=tools)

        class ReplayProvider(FakeModelProvider):
            async def generate(self, config, messages, tools=None):
                if any(m.get("role") == "tool" for m in messages):
                    self._config.tool_requests = []
                return await super().generate(config, messages, tools=tools)

        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "replay-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test."},
                    "dependencies": {
                        "model": {"ref": "fake-model"},
                        "tools": [{"name": "echo", "source": "native"}],
                    },
                    "runtime": {"error_policy": "retry"},
                },
            }
        )
        from micro_agent.tools import EchoTool

        class CountingEchoTool(EchoTool):
            async def execute(self, arguments):
                executions.append(1)
                return await super().execute(arguments)

        tool_requests = [
            {
                "name": "echo",
                "arguments": {"message": "submit", "idempotency_key": "order-1"},
            }
        ]
        crashing_runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=CrashProvider(
                    FakeModelConfig(response="x", tool_requests=tool_requests)
                ),
                operation_registry=registry,
                session_provider=session_provider,
                tool_registry={"echo": CountingEchoTool()},
            )
        )
        crashing_agent = DefaultMicroAgent(definition, crashing_runtime)
        await crashing_agent.initialize()
        await crashing_agent.start()
        request = AgentRequest(input={}, session_id="replay-session")
        with pytest.raises(Exception, match="suppressed"):  # noqa: B017 — partial failure
            await crashing_agent.invoke(request)
        assert len(executions) == 1
        await crashing_agent.stop()
        await crashing_agent.shutdown()
        await crashing_runtime.close()

        # The "restarted process": fresh runtime, same shared state.
        replay_runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=ReplayProvider(
                    FakeModelConfig(response="recovered", tool_requests=tool_requests)
                ),
                operation_registry=registry,
                session_provider=session_provider,
                tool_registry={"echo": CountingEchoTool()},
            )
        )
        replay_agent = DefaultMicroAgent(definition, replay_runtime)
        try:
            await replay_agent.initialize()
            await replay_agent.start()
            response = await replay_agent.invoke(request)
            assert response.status == "success"
            assert len(executions) == 1, "the side effect must not re-execute"
            deduped = response.output["tool_results"][0]
            assert deduped.get("was_deduplicated") is True
            assert deduped["output"] == {"echoed": "submit"}
        finally:
            await replay_agent.stop()
            await replay_agent.shutdown()
            await replay_runtime.close()
