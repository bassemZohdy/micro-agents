"""Framework-neutral control-flow errors for governed execution."""

from __future__ import annotations

from typing import Any

from micro_agent.security import PendingApproval


class ApprovalPausedError(Exception):
    """Internal marker: the invocation paused waiting for an approval."""

    def __init__(self, approval: PendingApproval) -> None:
        self.approval = approval


class RetrySuppressedAfterSideEffectError(RuntimeError):
    retryable = False
    """Prevent replaying an invocation after a non-read-only tool ran."""


class ApprovalRequiredError(Exception):
    """A governed tool needs a human decision before it may run.

    Raised by :class:`~micro_agent.governance.tools.GovernedTool` so any
    framework's tool loop can stop; the runtime adapter persists the pending
    request in the ``ApprovalStore`` and returns an ``approval_required`` response.
    """

    def __init__(self, tool_name: str, arguments: dict[str, Any], reason: str = "") -> None:
        super().__init__(f"tool '{tool_name}' requires approval")
        self.tool_name = tool_name
        self.arguments = arguments
        self.reason = reason
