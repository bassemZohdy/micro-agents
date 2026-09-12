"""Micro-Agent Tools.

Tool definition, runtime contract, and observability.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

ToolSideEffect = Literal["read_only", "idempotent", "unsafe"]
_TOOL_SIDE_EFFECTS = frozenset({"read_only", "idempotent", "unsafe"})


def normalize_tool_side_effect(value: str | None) -> ToolSideEffect:
    """Normalize untrusted metadata to a fail-closed side-effect class."""
    return value if value in _TOOL_SIDE_EFFECTS else "unsafe"  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------


@dataclass
class ToolMetadata:
    """Metadata for a tool.

    Native and discovered tools default to ``unsafe`` so an omitted
    declaration never weakens approval or idempotency enforcement.
    """

    name: str
    description: str | None = None
    source: str | None = None
    timeout_seconds: int | None = None
    side_effect: ToolSideEffect = "unsafe"


@dataclass
class ToolInputSchema:
    """Input schema for a tool."""

    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolOutputSchema:
    """Output schema for a tool."""

    parameters: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool Result
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """Result of a tool invocation."""

    output: Any = None
    error: str | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool Error
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Raised when a tool invocation fails."""

    def __init__(self, message: str, tool_name: str = "") -> None:
        super().__init__(message)
        self.tool_name = tool_name


# ---------------------------------------------------------------------------
# Tool Runtime Contract
# ---------------------------------------------------------------------------


class Tool(ABC):
    """Abstract tool interface."""

    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata:
        """Return tool metadata."""

    @property
    @abstractmethod
    def input_schema(self) -> ToolInputSchema:
        """Return input schema."""

    @property
    @abstractmethod
    def output_schema(self) -> ToolOutputSchema:
        """Return output schema."""

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the tool with given arguments."""


# ---------------------------------------------------------------------------
# Deterministic Example Tool
# ---------------------------------------------------------------------------


class EchoTool(Tool):
    """Deterministic example tool that echoes input back."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="echo",
            description="Echoes the input back as output.",
            source="native",
            timeout_seconds=5,
        )

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Message to echo."}},
                "required": ["message"],
            }
        )

    @property
    def output_schema(self) -> ToolOutputSchema:
        return ToolOutputSchema(
            parameters={
                "type": "object",
                "properties": {"echoed": {"type": "string", "description": "Echoed message."}},
            }
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        message = arguments.get("message", "")
        return ToolResult(output={"echoed": message})


class JsonParseTool(Tool):
    """Parse a bounded JSON document without performing side effects."""

    def __init__(self, max_bytes: int = 65_536) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be greater than zero")
        self._max_bytes = max_bytes

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="json_parse",
            description="Parses a bounded JSON document into a structured value.",
            source="native",
            timeout_seconds=5,
            side_effect="read_only",
        )

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            parameters={
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "description": "JSON document to parse.",
                    }
                },
                "required": ["document"],
                "additionalProperties": False,
            }
        )

    @property
    def output_schema(self) -> ToolOutputSchema:
        return ToolOutputSchema(
            parameters={
                "type": "object",
                "properties": {"value": {"description": "Parsed JSON value."}},
                "required": ["value"],
                "additionalProperties": False,
            }
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        document = arguments.get("document", "")
        if not isinstance(document, str):
            return ToolResult(output=None, error="document must be a string", is_error=True)
        size = len(document.encode("utf-8"))
        if size > self._max_bytes:
            return ToolResult(
                output=None,
                error=f"JSON document too large ({size} bytes > {self._max_bytes})",
                is_error=True,
            )
        try:
            value = json.loads(document)
        except json.JSONDecodeError as exc:
            return ToolResult(
                output=None,
                error=f"invalid JSON at position {exc.pos}",
                is_error=True,
            )
        return ToolResult(output={"value": value})


# ---------------------------------------------------------------------------
# Built-in native tool registry
# ---------------------------------------------------------------------------


def builtin_tool_registry() -> dict[str, Tool]:
    """Fresh instances of the native tools shipped in this distribution.

    Definition-declared tools are matched by name against this registry; MCP
    tools are resolved separately through the MCP connection manager.
    """
    return {"echo": EchoTool(), "json_parse": JsonParseTool()}
