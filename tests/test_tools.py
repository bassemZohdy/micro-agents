"""Tests for Micro-Agent Tools."""

import pytest

from micro_agent.tools import (
    EchoTool,
    Tool,
    ToolError,
    ToolMetadata,
    ToolResult,
)


class TestToolMetadata:
    """Test tool metadata."""

    def test_basic_metadata(self):
        meta = ToolMetadata(name="test-tool")
        assert meta.name == "test-tool"
        assert meta.description is None

    def test_full_metadata(self):
        meta = ToolMetadata(
            name="check",
            description="Check eligibility",
            source="native",
            timeout_seconds=10,
        )
        assert meta.source == "native"
        assert meta.timeout_seconds == 10


class TestToolResult:
    """Test tool result."""

    def test_success_result(self):
        result = ToolResult(output={"status": "ok"})
        assert result.is_error is False
        assert result.error is None

    def test_error_result(self):
        result = ToolResult(error="failed", is_error=True)
        assert result.is_error is True


class TestToolError:
    """Test tool error."""

    def test_error_message(self):
        err = ToolError("something broke", tool_name="my-tool")
        assert str(err) == "something broke"
        assert err.tool_name == "my-tool"


class TestToolInterface:
    """Test that Tool is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Tool()  # type: ignore[abstract]


class TestEchoTool:
    """Test the deterministic echo tool."""

    def test_metadata(self):
        tool = EchoTool()
        assert tool.metadata.name == "echo"
        assert tool.metadata.source == "native"

    def test_input_schema(self):
        tool = EchoTool()
        schema = tool.input_schema
        assert "message" in schema.parameters.get("properties", {})

    def test_output_schema(self):
        tool = EchoTool()
        schema = tool.output_schema
        assert "echoed" in schema.parameters.get("properties", {})

    @pytest.mark.asyncio
    async def test_execute(self):
        tool = EchoTool()
        result = await tool.execute({"message": "hello"})
        assert result.output == {"echoed": "hello"}
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_execute_empty(self):
        tool = EchoTool()
        result = await tool.execute({})
        assert result.output == {"echoed": ""}
