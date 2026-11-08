"""Governed tool execution that works under any agent framework.

:class:`ToolGovernor` applies Micro-Agents' deterministic controls to one tool
call: argument validation, platform policy, side-effect policy, idempotent
operation claiming and recording, timeouts, telemetry and audit. The reference
loop and the LangGraph adapter call it from ``GovernedRuntime._execute_tools``.

:class:`GovernedTool` wraps a :class:`~micro_agent.tools.Tool` with the same
governor so a framework that runs its *own* tool loop (and therefore never calls
``_execute_tools``) cannot bypass governance: the framework receives wrapped
tools. Per-invocation context (trace, labels, deadline) is bound with
:func:`use_tool_scope`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from micro_agent.governance.errors import ApprovalRequiredError, RetrySuppressedAfterSideEffectError
from micro_agent.governance.support import DEFAULT_TOOL_TIMEOUT_SECONDS, maybe_await
from micro_agent.observability import Telemetry
from micro_agent.security import (
    Operation,
    OperationRegistryProtocol,
    OperationResult,
    PolicyEvaluator,
    RetryClassification,
    get_invocation_identity,
)
from micro_agent.tools import (
    Tool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
    ToolResult,
    normalize_tool_side_effect,
)


class DeadlineLike(Protocol):
    """The slice of an invocation deadline the governor needs."""

    @property
    def expired(self) -> bool: ...

    def remaining(self) -> float | None: ...

    def run(self, operation: Any, cap: float | int | None = None) -> Any: ...


@dataclass
class ToolCallScope:
    """Per-invocation context for governed tool calls."""

    trace_id: str = ""
    parent_span_id: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    deadline: DeadlineLike | None = None
    #: Set when a human already approved this wave; approval gates are skipped
    #: but hard policy denials still apply.
    skip_side_effect_approval: bool = False
    #: Per-tool side-effect overrides declared by the definition.
    side_effects: dict[str, str] | None = None
    #: Mutable record of side-effecting tools attempted, used to suppress retries.
    side_effect_state: dict[str, Any] | None = None


_current_scope: ContextVar[ToolCallScope | None] = ContextVar(
    "micro_agent_tool_scope", default=None
)


@contextmanager
def use_tool_scope(scope: ToolCallScope) -> Iterator[ToolCallScope]:
    """Bind ``scope`` for governed tools called inside the block."""
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        _current_scope.reset(token)


def current_tool_scope() -> ToolCallScope:
    """Return the bound scope, or an empty one when none is bound."""
    return _current_scope.get() or ToolCallScope()


class ToolGovernor:
    """Applies policy, validation, idempotency, telemetry and audit to tool calls."""

    def __init__(
        self,
        *,
        telemetry: Telemetry,
        policy_evaluator: PolicyEvaluator | None = None,
        operation_registry: OperationRegistryProtocol | None = None,
        audit: Callable[..., None] | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._policy_evaluator = policy_evaluator
        self._operation_registry = operation_registry
        self._audit = audit

    # ------------------------------------------------------------------
    # Policy helpers
    # ------------------------------------------------------------------

    def deny(
        self,
        labels: dict[str, str],
        tool_name: str,
        reason: str,
        event: str = "policy.tool_denied",
    ) -> None:
        """Record a policy denial in telemetry, logs and the audit sink."""
        self._telemetry.increment("policy_denials_total", {**labels, "tool": tool_name})
        self._telemetry.logger.warning("tool denied by policy", tool=tool_name, reason=reason)
        if self._audit is not None:
            self._audit(event, agent=labels.get("agent"), tool=tool_name, reason=reason)

    def side_effect_of(self, tool: Tool, side_effects: dict[str, str] | None = None) -> str:
        """Effective side-effect class: definition override, else tool metadata."""
        name = tool.metadata.name
        return normalize_tool_side_effect(
            (side_effects or {}).get(name) or tool.metadata.side_effect
        )

    def requires_approval(self, tool: Tool, side_effects: dict[str, str] | None = None) -> bool:
        """Whether running ``tool`` now would wait on a human approval policy."""
        if self._policy_evaluator is None:
            return False
        name = tool.metadata.name
        if not self._policy_evaluator.evaluate_tool(name).allowed:
            return False
        if self.side_effect_of(tool, side_effects) == "read_only":
            return False
        return self._policy_evaluator.evaluate_side_effect(name).requires_approval

    def approval_needed(
        self,
        tools: dict[str, Tool],
        tool_requests: list[dict[str, Any]],
        side_effects: dict[str, str] | None = None,
    ) -> bool:
        """Whether any request in a tool wave is waiting on an approval policy."""
        for tool_request in tool_requests:
            tool = tools.get(str(tool_request.get("name", "")))
            if tool is not None and self.requires_approval(tool, side_effects):
                return True
        return False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run_request(
        self,
        tools: dict[str, Tool],
        tool_request: dict[str, Any],
        scope: ToolCallScope,
    ) -> dict[str, Any]:
        """Execute one model tool request and return its result record."""
        tool_name = tool_request.get("name", "")
        tool = tools.get(tool_name)
        if tool is None:
            return {
                "tool": tool_name,
                "tool_call_id": tool_request.get("id"),
                "output": None,
                "error": f"unknown tool: {tool_name}",
                "latency_ms": 0.0,
            }
        return await self.run(tool, tool_request, scope)

    async def run(
        self, tool: Tool, tool_request: dict[str, Any], scope: ToolCallScope
    ) -> dict[str, Any]:
        """Govern and execute one call of a resolved ``tool``."""
        tool_name = tool.metadata.name
        tool_call_id = tool_request.get("id")
        labels = scope.labels
        deadline = scope.deadline
        side_effect = self.side_effect_of(tool, scope.side_effects)
        retry_classification = {
            "read_only": RetryClassification.SAFE,
            "idempotent": RetryClassification.IDEMPOTENT,
            "unsafe": RetryClassification.UNSAFE,
        }[side_effect]

        def refused(error: str, **extra: Any) -> dict[str, Any]:
            return {
                "tool": tool_name,
                "tool_call_id": tool_call_id,
                "output": None,
                "error": error,
                "latency_ms": 0.0,
                **extra,
            }

        arguments = tool_request.get("arguments", {})
        validation_error = validate_tool_arguments(tool, arguments)
        if validation_error is not None:
            return refused(f"invalid tool arguments: {validation_error}")

        # Deterministic platform policy — evaluated outside the prompt so
        # prompt injection cannot override it.
        if self._policy_evaluator is not None:
            tool_decision = self._policy_evaluator.evaluate_tool(tool_name)
            if not tool_decision.allowed:
                self.deny(labels, tool_name, tool_decision.reason)
                return refused(f"denied by policy: {tool_decision.reason}", denied=True)
            if side_effect != "read_only":
                side_effect_decision = self._policy_evaluator.evaluate_side_effect(tool_name)
                approval_satisfied = (
                    scope.skip_side_effect_approval and side_effect_decision.requires_approval
                )
                if not side_effect_decision.allowed and not approval_satisfied:
                    self.deny(
                        labels,
                        tool_name,
                        side_effect_decision.reason,
                        event="policy.side_effect_denied",
                    )
                    return refused(
                        f"denied by side-effect policy: {side_effect_decision.reason}",
                        denied=True,
                        side_effect=side_effect,
                    )

        # Idempotency/deduplication for side-effect operations.
        registry = self._operation_registry
        operation = None
        if registry is not None and side_effect != "read_only":
            bound_identity = get_invocation_identity()
            tenant_id = (
                bound_identity.user.tenant_id
                if bound_identity is not None and bound_identity.user is not None
                else None
            )
            operation = new_operation(
                tool_name,
                arguments,
                tenant_id=tenant_id,
                retry_classification=retry_classification,
            )
            claim = getattr(registry, "claim", None)
            if callable(claim):
                claimed, prior = await maybe_await(claim(operation))
            else:
                duplicate = await maybe_await(registry.is_duplicate(operation))
                if duplicate:
                    find_result = registry.find_by_idempotency_key
                    if operation.tenant_id is None:
                        prior = await maybe_await(find_result(operation.idempotency_key or ""))
                    else:
                        prior = await maybe_await(
                            find_result(operation.idempotency_key or "", operation.tenant_id)
                        )
                else:
                    prior = None
                claimed = not duplicate
            if not claimed:
                return {
                    "tool": tool_name,
                    "tool_call_id": tool_call_id,
                    "output": prior.output if prior else None,
                    "error": (
                        "operation is already in progress"
                        if prior is not None and prior.status == "in_progress"
                        else (prior.error if prior else None)
                    ),
                    "latency_ms": 0.0,
                    "was_deduplicated": True,
                    "side_effect": side_effect,
                }

        span = self._telemetry.start_span(
            f"tool.{tool_name}",
            trace_id=scope.trace_id,
            parent_span_id=scope.parent_span_id,
        )
        tool_start = time.monotonic()
        if side_effect != "read_only" and scope.side_effect_state is not None:
            attempted_tools = scope.side_effect_state.setdefault("attempted_tools", [])
            if tool_name not in attempted_tools:
                attempted_tools.append(tool_name)
        try:
            timeout = tool.metadata.timeout_seconds or DEFAULT_TOOL_TIMEOUT_SECONDS
            if deadline is None:
                result = await asyncio.wait_for(tool.execute(arguments), timeout)
            else:
                result = await deadline.run(tool.execute(arguments), cap=timeout)
            output: Any = result.output
            error: str | None = result.error
            if result.is_error and not error:
                error = "tool reported an error"
        except TimeoutError:
            if deadline is not None and deadline.expired:
                self._telemetry.finish_span(span)
                raise
            output = None
            error = f"tool '{tool_name}' timed out"
        except Exception as exc:  # noqa: BLE001 — tool failures become results
            output = None
            error = str(exc)

        if registry is not None and operation is not None:
            try:
                await maybe_await(
                    registry.record(
                        operation,
                        OperationResult(
                            operation_id=operation.operation_id,
                            status="failed" if error else "success",
                            output=output,
                            error=error,
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — write outcome is unknown
                self._telemetry.increment(
                    "operation_record_errors_total", {**labels, "tool": tool_name}
                )
                span.add_event("operation.unknown_outcome", {"tool": tool_name})
                self._telemetry.finish_span(span)
                raise RetrySuppressedAfterSideEffectError(
                    f"operation result for side-effect tool '{tool_name}' is unknown"
                ) from exc

        tool_latency = round((time.monotonic() - tool_start) * 1000, 2)
        span.set_attribute("latency_ms", tool_latency)
        if error:
            span.add_event("tool.error", {"error": error})
        self._telemetry.finish_span(span)

        self._telemetry.increment("tool_calls_total", {**labels, "tool": tool_name})
        self._telemetry.record("tool_latency_ms", tool_latency, {**labels, "tool": tool_name})
        self._telemetry.observe_histogram(
            "tool_latency_histogram_ms",
            tool_latency,
            {**labels, "tool": tool_name},
        )
        return {
            "tool": tool_name,
            "tool_call_id": tool_call_id,
            "output": output,
            "error": error,
            "latency_ms": tool_latency,
            "side_effect": side_effect,
        }


class GovernedTool(Tool):
    """A tool whose every execution is governed, whichever framework calls it.

    The wrapped tool is exposed unchanged (metadata and schemas), so frameworks
    advertise it normally; ``execute`` routes through the :class:`ToolGovernor`
    using the scope bound by :func:`use_tool_scope`. A call that policy says
    needs approval raises :class:`ApprovalRequiredError` instead of running.
    """

    def __init__(self, inner: Tool, governor: ToolGovernor) -> None:
        self._inner = inner
        self._governor = governor

    @property
    def inner(self) -> Tool:
        return self._inner

    @property
    def metadata(self) -> ToolMetadata:
        return self._inner.metadata

    @property
    def input_schema(self) -> ToolInputSchema:
        return self._inner.input_schema

    @property
    def output_schema(self) -> ToolOutputSchema:
        return self._inner.output_schema

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        scope = current_tool_scope()
        if not scope.skip_side_effect_approval and self._governor.requires_approval(
            self._inner, scope.side_effects
        ):
            raise ApprovalRequiredError(self._inner.metadata.name, dict(arguments))
        record = await self._governor.run(
            self._inner,
            {"name": self._inner.metadata.name, "arguments": arguments},
            scope,
        )
        error = record.get("error")
        return ToolResult(
            output=record.get("output"),
            error=error,
            is_error=bool(error),
            metadata={
                key: record[key]
                for key in ("denied", "was_deduplicated", "side_effect", "latency_ms")
                if key in record
            },
        )


_ARGUMENT_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


def validate_tool_arguments(tool: Tool, arguments: Any) -> str | None:
    """Validate a tool request against the declared JSON Schema contract.

    Checks the argument envelope, required properties, and basic property
    types; detailed schema validation stays with the tool implementation.
    """
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    schema = tool.input_schema.parameters or {}
    for name in schema.get("required") or []:
        if name not in arguments:
            return f"missing required argument '{name}'"
    properties = schema.get("properties") or {}
    for name, value in arguments.items():
        expected = (properties.get(name) or {}).get("type")
        check = _ARGUMENT_TYPE_CHECKS.get(str(expected))
        if check is not None and not check(value):
            return f"argument '{name}' must be of type {expected}"
    return None


def new_operation(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    tenant_id: str | None = None,
    retry_classification: RetryClassification = RetryClassification.SAFE,
) -> Operation:
    """Build an Operation for a tool call; arguments may carry an idempotency key."""
    key = arguments.get("idempotency_key")
    return Operation(
        name=tool_name,
        arguments=arguments,
        idempotency_key=str(key) if key else None,
        tenant_id=tenant_id,
        retry_classification=retry_classification,
    )
