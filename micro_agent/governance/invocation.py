"""Invocation-level resilience shared by every runtime.

:class:`InvocationGovernor` applies the declared ``RuntimeSemantics`` to one
invocation: the circuit breaker, deadline accounting, retry with backoff and
jitter (never after a side effect), and the ``fallback`` error policy. It wraps
a runtime-supplied ``attempt`` callable, so it works for any orchestration: the
reference loop, the LangGraph graph and the Google ADK runner all go through the
same code and therefore fail, retry and trip identically.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from micro_agent.core import AgentRequest, AgentResponse
from micro_agent.definition import ErrorPolicy, MicroAgentDefinition
from micro_agent.governance.errors import (
    ApprovalPausedError,
    RetrySuppressedAfterSideEffectError,
)
from micro_agent.governance.support import InvocationDeadline
from micro_agent.observability import Telemetry
from micro_agent.resilience import CircuitBreaker, Retryability, classify_retry


async def wait_for_retry(
    delay_seconds: float,
    invocation_deadline: InvocationDeadline,
    retry_deadline: InvocationDeadline,
) -> None:
    """Sleep for a retry delay without exceeding either invocation budget."""
    if delay_seconds <= 0:
        if invocation_deadline.expired or retry_deadline.expired:
            raise TimeoutError("retry budget exceeded")
        return
    remaining = [
        value
        for value in (invocation_deadline.remaining(), retry_deadline.remaining())
        if value is not None
    ]
    if remaining:
        allowed = min(remaining)
        if allowed <= 0:
            raise TimeoutError("retry budget exceeded")
        if delay_seconds > allowed:
            await asyncio.sleep(allowed)
            raise TimeoutError("retry budget exceeded")
    await asyncio.sleep(delay_seconds)


class InvocationGovernor:
    """Circuit breaker, retry and fallback around one invocation attempt."""

    def __init__(
        self,
        *,
        telemetry: Callable[[], Telemetry],
        audit: Callable[..., None] | None = None,
    ) -> None:
        # Resolved per use so a runtime that swaps its telemetry stays consistent.
        self._telemetry_source = telemetry
        self._audit = audit
        #: Per-agent breakers, keyed by agent name.
        self.circuits: dict[str, CircuitBreaker] = {}

    @property
    def _telemetry(self) -> Telemetry:
        return self._telemetry_source()

    def circuit_for(self, definition: MicroAgentDefinition) -> CircuitBreaker | None:
        """Per-agent breaker from declared runtime semantics (None = disabled)."""
        semantics = definition.spec.runtime
        if semantics.circuit_breaker_failures is None or (
            semantics.circuit_breaker_cooldown_seconds is None
        ):
            return None
        breaker = self.circuits.get(definition.metadata.name)
        if breaker is None:
            breaker = CircuitBreaker(
                semantics.circuit_breaker_failures,
                semantics.circuit_breaker_cooldown_seconds,
            )
            self.circuits[definition.metadata.name] = breaker
        return breaker

    async def run(
        self,
        definition: MicroAgentDefinition,
        request: AgentRequest,
        *,
        attempt: Callable[[], Awaitable[AgentResponse]],
        deadline: InvocationDeadline,
        side_effect_state: dict[str, Any],
        streamed_output: Callable[[], bool] = lambda: False,
    ) -> AgentResponse:
        """Run ``attempt`` under the definition's declared resilience semantics.

        ``side_effect_state["attempted_tools"]`` is filled by governed tools; once
        non-empty, retries are suppressed. ``streamed_output`` reports whether
        output was already emitted to a consumer, which also forbids a retry.
        """
        semantics = definition.spec.runtime
        labels = {"agent": definition.metadata.name}
        retry_deadline = InvocationDeadline.from_seconds(semantics.retry_budget_seconds)

        breaker = self.circuit_for(definition)
        if breaker is not None:
            # Reject before any work while the circuit is open; after the
            # cooldown this allows one probe invocation.
            breaker.check(agent=definition.metadata.name)

        start_time = time.monotonic()
        try:
            response = await attempt()
        except TimeoutError:
            self._telemetry.increment("agent_invocation_errors_total", labels)
            self._telemetry.logger.error(
                "invocation deadline exceeded",
                request_id=request.request_id,
                session_id=request.session_id,
            )
            if breaker is not None and breaker.record_failure() == "open":
                self._telemetry.increment("circuit_breaker_trips_total", labels)
            raise
        except ApprovalPausedError as paused:
            # A pause is not an error: retry/fallback policies must not apply.
            approval = paused.approval
            pending_names = [str(tr.get("name", "")) for tr in approval.tool_requests]
            if self._audit is not None:
                self._audit(
                    "approval.requested",
                    agent=definition.metadata.name,
                    tools=pending_names,
                    continuation_id=approval.continuation_id,
                )
            self._telemetry.logger.warning(
                "invocation paused awaiting approval",
                request_id=request.request_id,
                tools=pending_names,
            )
            return AgentResponse(
                output={"content": "", "tool_results": approval.all_tool_results},
                request_id=approval.request_id or request.request_id,
                session_id=approval.session_id,
                status="approval_required",
                metadata={
                    "continuation_id": approval.continuation_id,
                    "pending_tools": pending_names,
                },
            )
        except Exception as exc:
            self._telemetry.increment("agent_invocation_errors_total", labels)
            if streamed_output():
                self._telemetry.increment("agent_retries_suppressed_total", labels)
                raise RuntimeError(
                    "invocation failed after streaming output was emitted; "
                    "retry/fallback suppressed"
                ) from exc
            self._telemetry.logger.error(
                "invocation failed",
                request_id=request.request_id,
                session_id=request.session_id,
                error=str(exc),
            )
            if breaker is not None and breaker.record_failure() == "open":
                self._telemetry.increment("circuit_breaker_trips_total", labels)
            if semantics.error_policy is ErrorPolicy.RETRY:
                response = await self._retry(
                    exc, semantics, labels, deadline, retry_deadline, attempt, side_effect_state
                )
            elif semantics.error_policy is ErrorPolicy.FALLBACK:
                response = AgentResponse(
                    output={},
                    request_id=request.request_id,
                    session_id=request.session_id,
                    status="error",
                    error=str(exc),
                    metadata={"error_policy": "fallback"},
                )
            else:
                raise

        latency_ms = round((time.monotonic() - start_time) * 1000, 2)
        self._telemetry.increment("agent_invocations_total", labels)
        self._telemetry.record("agent_invocation_latency_ms", latency_ms, labels)
        self._telemetry.observe_histogram(
            "agent_invocation_latency_histogram_ms", latency_ms, labels
        )
        if breaker is not None:
            breaker.record_success()
        return response

    async def _retry(
        self,
        exc: Exception,
        semantics: Any,
        labels: dict[str, str],
        deadline: InvocationDeadline,
        retry_deadline: InvocationDeadline,
        attempt: Callable[[], Awaitable[AgentResponse]],
        side_effect_state: dict[str, Any],
    ) -> AgentResponse:
        if isinstance(exc, RetrySuppressedAfterSideEffectError):
            raise exc
        if side_effect_state.get("attempted_tools", []):
            self._suppress(labels, side_effect_state, exc)
        retry_error: Exception = exc
        if classify_retry(retry_error) is Retryability.NON_RETRYABLE:
            # Deterministic failures (denials, contract violations,
            # authentication) cannot succeed on retry — fail now.
            raise retry_error from exc
        for retry_index in range(semantics.retry_max_attempts):
            if side_effect_state.get("attempted_tools", []):
                self._suppress(labels, side_effect_state, retry_error)
            if classify_retry(retry_error) is Retryability.NON_RETRYABLE:
                raise retry_error from exc
            delay = semantics.retry_backoff_seconds * (2**retry_index)
            if semantics.retry_jitter_seconds:
                delay += random.uniform(0, semantics.retry_jitter_seconds)
            await wait_for_retry(delay, deadline, retry_deadline)
            self._telemetry.increment("agent_retries_total", labels)
            try:
                return await attempt()
            except (TimeoutError, ApprovalPausedError, RetrySuppressedAfterSideEffectError):
                raise
            except Exception as retry_exc:
                self._telemetry.increment("agent_invocation_errors_total", labels)
                retry_error = retry_exc
        raise retry_error from exc

    def _suppress(
        self, labels: dict[str, str], side_effect_state: dict[str, Any], cause: Exception
    ) -> None:
        tool_names = ", ".join(str(name) for name in side_effect_state["attempted_tools"])
        self._telemetry.increment("agent_retries_suppressed_total", labels)
        raise RetrySuppressedAfterSideEffectError(
            f"invocation retry suppressed after side-effect tool execution: {tool_names}"
        ) from cause
