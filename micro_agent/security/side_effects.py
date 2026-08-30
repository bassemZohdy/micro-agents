"""Micro-Agent Safe Side Effects.

Operations with side effects should assume retries, failures,
and possible replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Operation Model
# ---------------------------------------------------------------------------


class RetryClassification(StrEnum):
    """Retry classification for operations."""

    SAFE = "safe"
    UNSAFE = "unsafe"
    IDEMPOTENT = "idempotent"


@dataclass
class Operation:
    """A side-effect operation with safety metadata."""

    operation_id: str = field(default_factory=lambda: str(uuid4()))
    idempotency_key: str | None = None
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    retry_classification: RetryClassification = RetryClassification.SAFE
    requires_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperationResult:
    """Result of a side-effect operation."""

    operation_id: str = ""
    status: str = "success"
    output: Any = None
    error: str | None = None
    was_deduplicated: bool = False


# ---------------------------------------------------------------------------
# Operation Registry (deduplication)
# ---------------------------------------------------------------------------


class OperationRegistry:
    """Registry for tracking operations and deduplication."""

    def __init__(self) -> None:
        self._completed: dict[str, OperationResult] = {}

    def record(self, operation: Operation, result: OperationResult) -> None:
        """Record a completed operation."""
        key = operation.idempotency_key or operation.operation_id
        self._completed[key] = result

    def find_by_idempotency_key(self, key: str) -> OperationResult | None:
        """Find a previously completed operation by idempotency key."""
        return self._completed.get(key)

    def is_duplicate(self, operation: Operation) -> bool:
        """Check if an operation has already been completed."""
        if operation.idempotency_key:
            return operation.idempotency_key in self._completed
        return False
