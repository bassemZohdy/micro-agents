"""Tool extension contract.

Deployments extend the tool registry two ways, in increasing precedence:

1. **Plugin packages** (recommended): a distribution exposes an entry point
   in the ``micro_agent.tools`` group whose value is ``module:attribute`` —
   either a :class:`~micro_agent.tools.Tool` subclass with a no-argument
   constructor or a zero-argument callable returning a Tool instance. Tools
   are keyed by their ``metadata.name``.

2. **Programmatic injection**: pass ``tool_registry`` on the runtime config
   (or through the bootstrap), which overrides plugin and built-in tools of
   the same name.

Definition-declared tools resolve against the merged registry by name; tools
that cannot be resolved fail before runtime creation.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from micro_agent.tools.tool import Tool, builtin_tool_registry


class ToolPluginError(RuntimeError):
    """Raised when a registered tool plugin cannot be loaded."""


def _instantiate(entry_point: Any) -> Tool:
    obj = entry_point.load()
    if isinstance(obj, type) and issubclass(obj, Tool):
        return obj()
    result = obj() if callable(obj) else obj
    if isinstance(result, Tool):
        return result
    raise ToolPluginError(
        f"tool plugin '{entry_point.name}' ({entry_point.value}) does not produce a Tool instance"
    )


def load_plugin_tools(
    *, entry_point_group: str = "micro_agent.tools", discovered: list[Any] | None = None
) -> dict[str, Tool]:
    """Load tools contributed by installed plugin packages.

    ``discovered`` is a test seam overriding the entry-point scan. Tools whose
    ``metadata.name`` collides keep the first loaded occurrence — entry-point
    order is environment-defined, so plugin authors must use unique names.
    """
    tools: dict[str, Tool] = builtin_tool_registry()
    entries: list[Any] = (
        discovered if discovered is not None else list(entry_points(group=entry_point_group))
    )
    for entry_point in entries:
        tool = _instantiate(entry_point)
        tools.setdefault(tool.metadata.name, tool)
    return tools


__all__ = ["ToolPluginError", "load_plugin_tools"]
