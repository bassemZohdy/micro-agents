"""Governance applies at the tool boundary, even for a framework-owned tool loop."""

from __future__ import annotations

from typing import Any

import pytest

from micro_agent.governance import (
    ApprovalRequiredError,
    GovernedTool,
    ToolCallScope,
    ToolGovernor,
    current_tool_scope,
    use_tool_scope,
)
from micro_agent.observability import Telemetry
from micro_agent.security import AgentPolicy, OperationRegistry, PolicyEvaluator
from micro_agent.tools import (
    Tool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
    ToolResult,
)


class _CountingTool(Tool):
    """A side-effecting tool that records how often it really ran."""

    def __init__(self, name: str = "charge", side_effect: str = "unsafe") -> None:
        self._name = name
        self._side_effect = side_effect
        self.calls: list[dict[str, Any]] = []

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name=self._name,
            description="test tool",
            source="native",
            side_effect=self._side_effect,  # type: ignore[arg-type]
        )

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            parameters={
                "type": "object",
                "properties": {
                    "amount": {"type": "integer"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["amount"],
            }
        )

    @property
    def output_schema(self) -> ToolOutputSchema:
        return ToolOutputSchema()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(output={"charged": arguments["amount"]})


def _governor(policy: AgentPolicy | None = None, registry: OperationRegistry | None = None):
    return ToolGovernor(
        telemetry=Telemetry.from_environment(),
        policy_evaluator=PolicyEvaluator(policy) if policy is not None else None,
        operation_registry=registry,
    )


async def _foreign_framework_loop(tool: Tool, arguments: dict[str, Any]) -> ToolResult:
    """Stand-in for a framework with its own tool loop: it only knows ``Tool``."""
    return await tool.execute(arguments)


def test_governed_tool_is_transparent_to_the_framework():
    inner = _CountingTool()
    tool = GovernedTool(inner, _governor())
    assert tool.metadata == inner.metadata
    assert tool.input_schema == inner.input_schema
    assert tool.output_schema == inner.output_schema
    assert tool.inner is inner


@pytest.mark.asyncio
async def test_a_framework_loop_cannot_bypass_policy_denial():
    inner = _CountingTool()
    tool = GovernedTool(inner, _governor(AgentPolicy(denied_tools=["charge"])))
    result = await _foreign_framework_loop(tool, {"amount": 5})
    assert result.is_error and "denied by policy" in (result.error or "")
    assert result.metadata["denied"] is True
    assert inner.calls == []


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_before_execution():
    inner = _CountingTool()
    tool = GovernedTool(inner, _governor())
    missing = await _foreign_framework_loop(tool, {})
    wrong_type = await _foreign_framework_loop(tool, {"amount": "five"})
    assert "missing required argument 'amount'" in (missing.error or "")
    assert "must be of type integer" in (wrong_type.error or "")
    assert inner.calls == []


@pytest.mark.asyncio
async def test_allowed_calls_run_and_report_the_side_effect_class():
    inner = _CountingTool()
    tool = GovernedTool(inner, _governor(AgentPolicy()))
    result = await _foreign_framework_loop(tool, {"amount": 7})
    assert not result.is_error and result.output == {"charged": 7}
    assert result.metadata["side_effect"] == "unsafe"
    assert inner.calls == [{"amount": 7}]


@pytest.mark.asyncio
async def test_side_effect_operations_are_deduplicated_by_idempotency_key():
    inner = _CountingTool()
    tool = GovernedTool(inner, _governor(registry=OperationRegistry()))
    arguments = {"amount": 9, "idempotency_key": "order-1"}
    first = await _foreign_framework_loop(tool, arguments)
    second = await _foreign_framework_loop(tool, arguments)
    assert first.output == second.output == {"charged": 9}
    assert second.metadata.get("was_deduplicated") is True
    assert len(inner.calls) == 1  # the charge happened once


@pytest.mark.asyncio
async def test_read_only_tools_skip_idempotency_and_approval():
    inner = _CountingTool("lookup", side_effect="read_only")
    tool = GovernedTool(
        inner, _governor(AgentPolicy(approval_required=True), registry=OperationRegistry())
    )
    first = await _foreign_framework_loop(tool, {"amount": 1, "idempotency_key": "k"})
    second = await _foreign_framework_loop(tool, {"amount": 1, "idempotency_key": "k"})
    assert not first.is_error and not second.is_error
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_a_tool_needing_approval_stops_the_framework_loop():
    inner = _CountingTool()
    tool = GovernedTool(inner, _governor(AgentPolicy(approval_required=True)))
    with pytest.raises(ApprovalRequiredError) as raised:
        await _foreign_framework_loop(tool, {"amount": 3})
    assert raised.value.tool_name == "charge" and raised.value.arguments == {"amount": 3}
    assert inner.calls == []


@pytest.mark.asyncio
async def test_an_approved_wave_runs_but_hard_denials_still_apply():
    inner = _CountingTool()
    approved = ToolCallScope(skip_side_effect_approval=True)
    tool = GovernedTool(inner, _governor(AgentPolicy(approval_required=True)))
    with use_tool_scope(approved):
        result = await _foreign_framework_loop(tool, {"amount": 4})
    assert not result.is_error and inner.calls == [{"amount": 4}]

    denied = GovernedTool(
        _CountingTool(), _governor(AgentPolicy(approval_required=True, denied_tools=["charge"]))
    )
    with use_tool_scope(approved):
        result = await _foreign_framework_loop(denied, {"amount": 4})
    assert "denied by policy" in (result.error or "")


@pytest.mark.asyncio
async def test_scope_binding_is_restored_after_the_block():
    assert current_tool_scope().skip_side_effect_approval is False
    with use_tool_scope(ToolCallScope(skip_side_effect_approval=True)):
        assert current_tool_scope().skip_side_effect_approval is True
    assert current_tool_scope().skip_side_effect_approval is False


@pytest.mark.asyncio
async def test_side_effecting_attempts_are_recorded_for_retry_suppression():
    state: dict[str, Any] = {}
    tool = GovernedTool(_CountingTool(), _governor())
    with use_tool_scope(ToolCallScope(side_effect_state=state)):
        await _foreign_framework_loop(tool, {"amount": 2})
    assert state["attempted_tools"] == ["charge"]


def test_approval_needed_for_a_tool_wave_matches_policy():
    governor = _governor(AgentPolicy(approval_required=True))
    tools: dict[str, Tool] = {
        "charge": _CountingTool(),
        "lookup": _CountingTool("lookup", "read_only"),
    }
    assert governor.approval_needed(tools, [{"name": "charge"}]) is True
    assert governor.approval_needed(tools, [{"name": "lookup"}, {"name": "ghost"}]) is False
    assert _governor().approval_needed(tools, [{"name": "charge"}]) is False
