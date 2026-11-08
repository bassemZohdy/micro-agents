"""The capability-gap report: renderer behavior and docs that cannot drift."""

from __future__ import annotations

import importlib.util

import pytest

from micro_agent.runtime import RuntimeCapabilities
from micro_agent.runtime.gaps import CAPABILITY_LABELS, render_capability_gaps
from tools import runtime_capabilities


def test_the_report_shows_native_provided_and_missing_capabilities():
    report = render_capability_gaps(
        {
            "a": RuntimeCapabilities(streaming=True, checkpointing=True),
            "b": RuntimeCapabilities(streaming=True, checkpointing=True).with_provided(
                "checkpointing"
            ),
            "c": RuntimeCapabilities(),
        }
    )
    rows = {line.split("|")[1].strip(): line for line in report.splitlines() if "|" in line}
    assert rows["Streaming"].split("|")[2:5] == [" native ", " native ", " no "]
    assert "**provided**" in rows["Checkpoint / resume"]
    assert "Gaps filled by the governance layer: `b`: checkpoint / resume." in report
    assert "streaming (`c`)" in report
    assert "Uniform guarantees covered by conformance cases" in report.splitlines()[-1]


def test_a_report_with_nothing_to_fill_says_so():
    full = RuntimeCapabilities(**{k: True for k in CAPABILITY_LABELS})
    report = render_capability_gaps({"only": full})
    assert "Gaps filled by the governance layer: none." in report
    assert "Gaps left open: none." in report


def test_the_embedded_block_is_extracted_between_its_markers():
    text = f"before\n{runtime_capabilities.START}\nBODY\n{runtime_capabilities.END}\nafter"
    assert runtime_capabilities.embedded(text) == "BODY"


@pytest.mark.skipif(
    not (importlib.util.find_spec("google.adk") and importlib.util.find_spec("langgraph")),
    reason="needs the adk and langgraph extras to build every runtime",
)
def test_docs_runtimes_capability_table_matches_the_code():
    text = runtime_capabilities.DOC.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert runtime_capabilities.embedded(text) == runtime_capabilities.report(), (
        "docs/RUNTIMES.md is stale; run: python tools/runtime_capabilities.py --write"
    )
