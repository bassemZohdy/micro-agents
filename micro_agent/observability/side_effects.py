"""Backward-compatibility shim — moved to :mod:`micro_agent.security.side_effects`."""

from micro_agent.security.side_effects import (
    Operation,
    OperationRegistry,
    OperationResult,
    RetryClassification,
)

__all__ = [
    "Operation",
    "OperationRegistry",
    "OperationResult",
    "RetryClassification",
]
