"""Micro-Agent Safe Side Effects.

Operations with side effects should assume retries, failures,
and possible replay.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
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
        self._in_progress: dict[str, str] = {}

    def claim(self, operation: Operation) -> tuple[bool, OperationResult | None]:
        """Atomically claim an idempotent operation in this process.

        The synchronous registry is intentionally kept as a useful local
        implementation. Redis-backed deployments use the same contract with
        an atomic ``SET NX`` claim across processes.
        """
        key = operation.idempotency_key
        if not key:
            return True, None
        prior = self._completed.get(key)
        if prior is not None:
            return False, prior
        if key in self._in_progress:
            return False, OperationResult(
                operation_id=self._in_progress[key],
                status="in_progress",
            )
        self._in_progress[key] = operation.operation_id
        return True, None

    def record(self, operation: Operation, result: OperationResult) -> None:
        """Record a completed operation."""
        key = operation.idempotency_key or operation.operation_id
        self._completed[key] = result
        if operation.idempotency_key:
            self._in_progress.pop(operation.idempotency_key, None)

    def find_by_idempotency_key(self, key: str) -> OperationResult | None:
        """Find a previously completed operation by idempotency key."""
        return self._completed.get(key)

    def is_duplicate(self, operation: Operation) -> bool:
        """Check if an operation has already been completed."""
        if operation.idempotency_key:
            return operation.idempotency_key in self._completed
        return False

    def health_check(self) -> bool:
        """Local registries are always available."""
        return True

    def aclose(self) -> None:
        """Keep the lifecycle contract shared with external registries."""
        return None


class OperationRegistryProtocol(Protocol):
    """Sync/async contract accepted by runtimes for operation storage."""

    def claim(
        self, operation: Operation
    ) -> tuple[bool, OperationResult | None] | Awaitable[tuple[bool, OperationResult | None]]:
        """Claim an operation or return the prior/in-progress result."""

    def is_duplicate(self, operation: Operation) -> bool | Awaitable[bool]:
        """Return whether an operation already has a stored result."""

    def find_by_idempotency_key(
        self, key: str
    ) -> OperationResult | None | Awaitable[OperationResult | None]:
        """Return a prior operation result when present."""

    def record(self, operation: Operation, result: OperationResult) -> None | Awaitable[None]:
        """Persist the completed operation result."""

    def health_check(self) -> bool | Awaitable[bool]:
        """Probe operation storage readiness."""

    def aclose(self) -> None | Awaitable[None]:
        """Release operation storage resources."""
