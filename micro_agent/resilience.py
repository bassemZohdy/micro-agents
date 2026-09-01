"""Resilience primitives: retryable-error taxonomy and circuit breaking.

The taxonomy classifies failures so bounded retries stop wasting attempts on
deterministic errors (denials, contract violations, authentication): those
fail immediately, transient connection failures retry, and unrecognized
errors keep the historical retry behavior. Exceptions may declare their
retryability explicitly with a ``retryable`` attribute.

The circuit breaker watches consecutive invocation failures per agent:
after ``threshold`` consecutive failures it opens and rejects calls with
:class:`CircuitOpenError` (a connection error, mapped to the stable 503
contract) until the cooldown elapses, then allows one probe call whose
outcome closes or reopens the circuit.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from time import monotonic
from typing import Any

from micro_agent.core import (
    AuthenticationError,
    ContinuationNotFoundError,
    DependencyUnavailableError,
)


class Retryability(StrEnum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class CircuitOpenError(DependencyUnavailableError):
    """Raised when the circuit breaker rejects a call while open.

    Subclasses the stable dependency-unavailable error so the HTTP layer
    maps it to 503 without leaking breaker internals.
    """


_DETERMINISTIC_ERRORS: tuple[type[BaseException], ...] = (
    AuthenticationError,
    ContinuationNotFoundError,
    PermissionError,
    ValueError,
    TypeError,
    ArithmeticError,
)


def classify_retry(exc: BaseException) -> Retryability:
    """Classify an exception for the bounded-retry loop.

    An explicit ``retryable`` attribute on the exception wins. Otherwise
    deterministic programming/auth failures are non-retryable, transport and
    dependency failures are retryable, and anything unrecognized keeps the
    historical behavior (retryable).
    """
    declared = getattr(exc, "retryable", None)
    if declared is True:
        return Retryability.RETRYABLE
    if declared is False:
        return Retryability.NON_RETRYABLE
    if isinstance(exc, TimeoutError):
        # The invocation deadline is shared across attempts; a timeout has
        # already consumed the budget and retrying cannot recover it.
        return Retryability.NON_RETRYABLE
    if isinstance(exc, _DETERMINISTIC_ERRORS):
        return Retryability.NON_RETRYABLE
    if isinstance(exc, (ConnectionError, OSError)):
        return Retryability.RETRYABLE
    return Retryability.RETRYABLE


class CircuitBreaker:
    """Per-agent closed/open/half-open breaker over consecutive failures."""

    def __init__(
        self,
        threshold: int,
        cooldown_seconds: float,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("circuit breaker threshold must be at least 1")
        if cooldown_seconds <= 0:
            raise ValueError("circuit breaker cooldown must be positive")
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._cooldown:
            return "half_open"
        return "open"

    def check(self, *, agent: str = "") -> None:
        """Raise :class:`CircuitOpenError` while the circuit is open."""
        if self.state != "open":
            if self.state == "half_open":
                self._half_open = True
            return
        raise CircuitOpenError(
            f"circuit breaker open for agent '{agent or 'unknown'}' after "
            f"{self._consecutive_failures} consecutive failures"
        )

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open = False

    def record_failure(self) -> str:
        """Record a failure; returns the resulting state."""
        self._consecutive_failures += 1
        if self._half_open:
            return self._trip()
        if self._consecutive_failures >= self._threshold and self._opened_at is None:
            return self._trip()
        return self.state

    def _trip(self) -> str:
        self._opened_at = self._clock()
        self._half_open = False
        return "open"

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consecutive_failures": self._consecutive_failures,
        }


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "Retryability",
    "classify_retry",
]
