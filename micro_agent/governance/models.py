"""Governed model access that behaves the same under any agent framework.

:class:`GovernedModelProvider` wraps a Micro-Agent :class:`ModelProvider` and
applies the per-call controls every runtime must share: the invocation deadline
(capped by the model's own timeout), a ``model.generate`` span, latency
histograms and token/cost telemetry, and streaming delta delivery. Per-invocation
context (trace, labels, deadline, delta sink, usage accumulator) is bound with
:func:`use_model_scope`, so a framework that calls the provider from inside its
own loop gets identical behavior without the adapter re-implementing it.

Invocation-level retry, fallback and circuit breaking live in
:class:`~micro_agent.governance.invocation.InvocationGovernor`; this class owns
the *call* boundary.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from micro_agent.governance.tools import DeadlineLike
from micro_agent.models import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ModelStreamEvent,
    ProviderCapabilities,
)
from micro_agent.observability import Telemetry
from micro_agent.observability.telemetry import TraceSpan


@dataclass
class ModelCallScope:
    """Per-invocation context for governed model calls."""

    trace_id: str = ""
    parent_span_id: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    deadline: DeadlineLike | None = None
    #: Receives streamed text deltas when the caller wants them.
    emit_delta: Callable[[str], Awaitable[None]] | None = None
    #: Token counts summed over every model call in the invocation.
    usage: dict[str, int] = field(default_factory=dict)
    #: Model calls made so far in the invocation (labels the span).
    calls: int = 0


_current_scope: ContextVar[ModelCallScope | None] = ContextVar(
    "micro_agent_model_scope", default=None
)


@contextmanager
def use_model_scope(scope: ModelCallScope) -> Iterator[ModelCallScope]:
    """Bind ``scope`` for governed model calls made inside the block."""
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        _current_scope.reset(token)


def current_model_scope() -> ModelCallScope | None:
    """Return the bound scope, or ``None`` when none is bound."""
    return _current_scope.get()


def add_usage(total: dict[str, int], usage: dict[str, int]) -> dict[str, int]:
    """Add ``usage`` into ``total`` in place and return it."""
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
    return total


class GovernedModelProvider(ModelProvider):
    """A :class:`ModelProvider` that applies Micro-Agents' call-level controls."""

    def __init__(self, inner: ModelProvider, telemetry: Callable[[], Telemetry]) -> None:
        self._inner = inner
        self._telemetry_source = telemetry

    @property
    def inner(self) -> ModelProvider:
        """The wrapped provider."""
        return self._inner

    @property
    def _telemetry(self) -> Telemetry:
        return self._telemetry_source()

    def capabilities(self) -> ProviderCapabilities:
        return self._inner.capabilities()

    async def health_check(self) -> bool:
        return await self._inner.health_check()

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate once; when the scope wants deltas and the model streams, stream."""
        scope = current_model_scope()
        if scope is None:
            return await self._inner.generate(config, messages, tools=tools)
        if scope.emit_delta is not None and self._inner.capabilities().streaming:
            return await self._observed(
                scope, config, self._consume(scope, config, messages, tools)
            )
        return await self._observed(
            scope,
            config,
            self._inner.generate(config, messages, tools=tools),
        )

    async def stream(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream events, bounded by the deadline and recorded like a normal call."""
        scope = current_model_scope()
        if scope is None:
            async for event in self._inner.stream(config, messages, tools=tools):
                yield event
            return
        span = self._start_span(scope)
        started = time.monotonic()
        final: ModelResponse | None = None
        try:
            budget = self._budget(scope, config)
            async with asyncio.timeout(budget):
                async for event in self._inner.stream(config, messages, tools=tools):
                    if event.response is not None:
                        final = event.response
                    yield event
        except TimeoutError as exc:
            self._fail(scope, span, exc)
            raise TimeoutError("invocation deadline exceeded") from exc
        except Exception as exc:
            self._fail(scope, span, exc)
            raise
        if final is None:
            error = RuntimeError("model stream ended without a final response")
            self._fail(scope, span, error)
            raise error
        self._finish(scope, span, started, final)

    # ------------------------------------------------------------------

    async def _consume(
        self,
        scope: ModelCallScope,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ModelResponse:
        final: ModelResponse | None = None
        async for event in self._inner.stream(config, messages, tools=tools):
            if event.delta and scope.emit_delta is not None:
                await scope.emit_delta(event.delta)
            if event.response is not None:
                final = event.response
        if final is None:
            raise RuntimeError("model stream ended without a final response")
        return final

    async def _observed(
        self,
        scope: ModelCallScope,
        config: ModelConfig,
        call: Awaitable[ModelResponse],
    ) -> ModelResponse:
        span = self._start_span(scope)
        started = time.monotonic()
        response: ModelResponse
        try:
            if scope.deadline is not None:
                response = await scope.deadline.run(call, cap=config.timeout_seconds)
            elif config.timeout_seconds is not None:
                response = await asyncio.wait_for(call, timeout=config.timeout_seconds)
            else:
                response = await call
        except Exception as exc:
            self._fail(scope, span, exc)
            raise
        self._finish(scope, span, started, response)
        return response

    def _budget(self, scope: ModelCallScope, config: ModelConfig) -> float | None:
        remaining = scope.deadline.remaining() if scope.deadline is not None else None
        caps = [v for v in (remaining, config.timeout_seconds) if v is not None]
        if not caps:
            return None
        if min(caps) <= 0:
            raise TimeoutError("invocation deadline exceeded")
        return float(min(caps))

    def _start_span(self, scope: ModelCallScope) -> TraceSpan:
        scope.calls += 1
        return self._telemetry.start_span(
            "model.generate",
            trace_id=scope.trace_id,
            parent_span_id=scope.parent_span_id,
            attributes={"iteration": scope.calls},
        )

    def _fail(self, scope: ModelCallScope, span: TraceSpan, exc: BaseException) -> None:
        span.add_event("model.error", {"error": str(exc)})
        self._telemetry.finish_span(span)

    def _finish(
        self,
        scope: ModelCallScope,
        span: TraceSpan,
        started: float,
        response: ModelResponse,
    ) -> None:
        latency = round((time.monotonic() - started) * 1000, 2)
        span.set_attribute("latency_ms", latency)
        span.add_event("model.response", {"finish_reason": response.finish_reason})
        self._telemetry.finish_span(span)
        self._telemetry.record("model_latency_ms", latency, scope.labels)
        self._telemetry.observe_histogram("model_latency_histogram_ms", latency, scope.labels)
        self._telemetry.record_model_usage(response.usage, scope.labels)
        add_usage(scope.usage, response.usage)
