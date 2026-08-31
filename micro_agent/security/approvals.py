"""Approval/confirmation continuation state.

When a policy requires approval for a side-effect operation, the runtime
pauses the invocation instead of permanently denying it: the pending tool
requests and the conversation state are stored under a continuation id, and
the caller resumes (approve) or cancels (deny) the invocation with that id.
The store is an SPI — the built-in in-memory store keeps the default
deployment dependency-free; production stores arrive with the state-provider
work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any

_DEFAULT_APPROVAL_TTL_SECONDS = 300.0


@dataclass
class PendingApproval:
    """A paused invocation waiting for an approval decision."""

    continuation_id: str
    agent_id: str
    tool_requests: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    all_tool_results: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    request_id: str | None = None
    session_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    expires_at: float | None = None

    def with_ttl(self, ttl_seconds: float) -> PendingApproval:
        return replace(self, expires_at=monotonic() + ttl_seconds)

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or monotonic()) >= self.expires_at


class ApprovalStore(ABC):
    """Stores pending approvals between the pause and the decision."""

    @abstractmethod
    async def save(self, approval: PendingApproval) -> None:
        """Store a pending approval."""

    @abstractmethod
    async def get(self, continuation_id: str) -> PendingApproval | None:
        """Return a non-expired pending approval, or ``None``."""

    @abstractmethod
    async def delete(self, continuation_id: str) -> None:
        """Consume a pending approval."""


class InMemoryApprovalStore(ApprovalStore):
    """Process-local pending-approval store with TTL expiry."""

    def __init__(self, default_ttl_seconds: float = _DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self._default_ttl = default_ttl_seconds
        self._approvals: dict[str, PendingApproval] = {}

    async def save(self, approval: PendingApproval) -> None:
        if approval.expires_at is None:
            approval = approval.with_ttl(self._default_ttl)
        self._approvals[approval.continuation_id] = approval

    async def get(self, continuation_id: str) -> PendingApproval | None:
        approval = self._approvals.get(continuation_id)
        if approval is None:
            return None
        if approval.expired():
            del self._approvals[continuation_id]
            return None
        return approval

    async def delete(self, continuation_id: str) -> None:
        self._approvals.pop(continuation_id, None)


__all__ = [
    "ApprovalStore",
    "InMemoryApprovalStore",
    "PendingApproval",
]
