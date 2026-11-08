"""Capability-gap report generated from what runtimes actually report.

``docs/RUNTIMES.md`` embeds this report between markers and a test fails when
the embedded copy differs from the generated one, so the documentation cannot
drift from ``RuntimeCapabilities``. The renderer is a pure function over
already-collected capabilities; building the runtimes is the caller's job (see
``tools/runtime_capabilities.py``).
"""

from __future__ import annotations

from collections.abc import Mapping

from micro_agent.runtime.guarantees import UNIFORM_GUARANTEES, guarantees_by_status
from micro_agent.runtime.runtime import RuntimeCapabilities

#: Human labels, in table order.
CAPABILITY_LABELS: dict[str, str] = {
    "streaming": "Streaming",
    "structured_output": "Structured output",
    "memory": "Memory",
    "mcp": "MCP tools",
    "a2a": "A2A",
    "checkpointing": "Checkpoint / resume",
}

_CELL = {"native": "native", "provided": "**provided**", "none": "no"}


def render_capability_gaps(capabilities: Mapping[str, RuntimeCapabilities]) -> str:
    """Render the capability matrix, the gaps the layer fills and the gaps left open."""
    names = list(capabilities)
    lines = [
        "| Capability | " + " | ".join(f"`{n}`" for n in names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for key, label in CAPABILITY_LABELS.items():
        cells = [_CELL[capabilities[n].source(key)] for n in names]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    filled = [
        f"`{n}`: {CAPABILITY_LABELS[key].lower()}"
        for n in names
        for key in CAPABILITY_LABELS
        if capabilities[n].source(key) == "provided"
    ]
    open_gaps = [
        f"{CAPABILITY_LABELS[key].lower()} (`{'`, `'.join(missing)}`)"
        for key in CAPABILITY_LABELS
        if (missing := [n for n in names if capabilities[n].source(key) == "none"])
    ]
    covered = len(guarantees_by_status("covered"))
    lines += [
        "",
        "**native** means the framework or the reference loop supplies it; "
        "**provided** means the Micro-Agents governance layer supplies it because the "
        "framework does not; no means not available.",
        "",
        "Gaps filled by the governance layer: " + (", ".join(filled) if filled else "none") + ".",
        "",
        "Gaps left open: " + ("; ".join(open_gaps) if open_gaps else "none") + ".",
        "",
        f"Uniform guarantees covered by conformance cases: {covered} of {len(UNIFORM_GUARANTEES)}.",
    ]
    return "\n".join(lines)


__all__ = ["CAPABILITY_LABELS", "render_capability_gaps"]
