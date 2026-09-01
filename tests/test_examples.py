"""Executable-example enforcement (P2).

Any example whose first line marks it ``Executable`` must load, build a
runtime from configuration alone, start, and serve an invocation — the
"executable as written" guarantee. Conceptual examples are only parsed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from micro_agent.config import build_runtime
from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.definition import load_definition_from_file
from micro_agent.tools import EchoTool, ToolMetadata


class PluginTool(EchoTool):
    @property
    def metadata(self):  # type: ignore[override]
        return ToolMetadata(name="plugin-echo", description="plugin tool")


def plugin_tool_factory() -> EchoTool:
    return PluginTool()


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
EXECUTABLE_MARKER = "Executable"


def _examples() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in sorted(EXAMPLES_DIR.glob("*.yaml"))}


def test_examples_exist_and_are_labeled():
    examples = _examples()
    assert len(examples) >= 3
    for path, text in examples.items():
        first_line = text.splitlines()[0].lower()
        labeled = (
            "executable" in first_line
            or "conceptual" in first_line
            or "schema example" in first_line
        )
        assert labeled, f"{path.name} must declare its status on the first line"


class TestExecutableExamples:
    """Examples marked Executable boot and serve without code changes."""

    def _executable() -> list:
        return [p for p, t in _examples().items() if t.splitlines()[0].startswith("# Executable")]

    @pytest.mark.parametrize("path", _executable())
    @pytest.mark.asyncio
    async def test_executable_example_boots_and_serves(self, path):
        definition = load_definition_from_file(path)
        bootstrap = build_runtime(definition)
        agent = DefaultMicroAgent(definition, bootstrap.runtime)
        try:
            await agent.initialize()
            await agent.start()
            response = await agent.invoke(AgentRequest(input={"question": "ping"}))
            assert response.status == "success"
        finally:
            await agent.stop()
            await agent.shutdown()
            await bootstrap.runtime.close()


class TestToolPluginContract:
    """Plugin packages contribute tools through entry points."""

    def test_plugin_tools_load_and_merge_over_builtins(self):
        from importlib.metadata import EntryPoint

        from micro_agent.tools.plugin import load_plugin_tools

        entry_point = EntryPoint(
            name="plugin-echo",
            value="tests.test_examples:plugin_tool_factory",
            group="micro_agent.tools",
        )
        tools = load_plugin_tools(discovered=[entry_point])
        assert "echo" in tools
        assert "plugin-echo" in tools
        assert tools["plugin-echo"].metadata.description == "plugin tool"

    def test_invalid_plugin_raises_clear_error(self):
        from importlib.metadata import EntryPoint

        from micro_agent.tools.plugin import ToolPluginError, load_plugin_tools

        entry_point = EntryPoint(
            name="broken",
            value="tests.test_examples:not_a_tool",
            group="micro_agent.tools",
        )
        with pytest.raises(ToolPluginError, match="does not produce a Tool"):
            load_plugin_tools(discovered=[entry_point])


not_a_tool = 42
