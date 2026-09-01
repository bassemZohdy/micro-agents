"""Shared state-provider errors and versioning helpers."""

from __future__ import annotations


class StateConflictError(RuntimeError):
    """Raised when a versioned state update loses an optimistic race."""

    def __init__(self, resource: str, key: str, expected: int, actual: int) -> None:
        self.resource = resource
        self.key = key
        self.expected_version = expected
        self.actual_version = actual
        super().__init__(
            f"{resource} '{key}' version conflict: expected {expected}, found {actual}"
        )


# Descriptive alias for callers that use concurrency terminology.
ConcurrencyConflictError = StateConflictError


__all__ = ["ConcurrencyConflictError", "StateConflictError"]
