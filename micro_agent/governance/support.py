"""Small framework-neutral helpers shared by the governance modules."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, TypeVar

DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
MAX_SESSION_HISTORY_MESSAGES = 20
_T = TypeVar("_T")


async def maybe_await(value: _T | Awaitable[_T]) -> _T:
    """Normalize sync local providers and async external providers."""
    if inspect.isawaitable(value):
        return await value
    return value


def call_with_supported_kwargs(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Call an optional provider extension without breaking legacy providers."""
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return method(*args, **kwargs)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return method(*args, **kwargs)
    supported = {name: value for name, value in kwargs.items() if name in parameters}
    return method(*args, **supported)


@dataclass(frozen=True)
class InvocationDeadline:
    """Absolute monotonic deadline shared by every invocation operation."""

    expires_at: float | None

    @classmethod
    def from_seconds(cls, *timeouts: float | int | None) -> InvocationDeadline:
        """Build a deadline from the shortest configured timeout."""
        configured = [float(value) for value in timeouts if value is not None]
        if not configured:
            return cls(None)
        return cls(time.monotonic() + min(configured))

    def remaining(self) -> float | None:
        """Return remaining seconds, or ``None`` when no deadline is set."""
        if self.expires_at is None:
            return None
        return self.expires_at - time.monotonic()

    @property
    def expired(self) -> bool:
        """Whether the deadline has elapsed."""
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    async def run(self, operation: Awaitable[_T], cap: float | int | None = None) -> _T:
        """Await an operation using the remaining budget and an optional cap."""
        remaining = self.remaining()
        deadline_controls_timeout = cap is None
        if cap is not None:
            deadline_controls_timeout = remaining is not None and float(cap) >= remaining
            remaining = min(float(cap), remaining) if remaining is not None else float(cap)
        if remaining is None:
            return await operation
        if remaining <= 0:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise TimeoutError("invocation deadline exceeded")
        try:
            return await asyncio.wait_for(operation, timeout=remaining)
        except TimeoutError as exc:
            if deadline_controls_timeout or self.expired:
                raise TimeoutError("invocation deadline exceeded") from exc
            raise
