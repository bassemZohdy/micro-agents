"""Capability sources and the cross-runtime guarantee list."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from micro_agent.runtime import (
    UNIFORM_GUARANTEES,
    Guarantee,
    RuntimeCapabilities,
    guarantees_by_status,
)

CONFORMANCE = Path(__file__).parent / "test_runtime_conformance.py"


def test_capabilities_default_to_none_and_stay_backwards_compatible():
    caps = RuntimeCapabilities(streaming=True)
    assert caps.provided == frozenset()
    assert caps.supports("streaming") and not caps.supports("checkpointing")
    assert caps.as_dict()["checkpointing"] is False
    assert set(caps.as_dict()) == {
        "streaming", "memory", "mcp", "a2a", "structured_output", "checkpointing",
    }  # fmt: skip
    assert caps.sources()["streaming"] == "native"
    assert caps.sources()["checkpointing"] == "none"


def test_provided_capabilities_are_effective_and_labelled():
    caps = RuntimeCapabilities(streaming=True).with_provided("checkpointing")
    assert caps.checkpointing is True and caps.supports("checkpointing")
    assert caps.as_dict()["checkpointing"] is True
    assert caps.source("checkpointing") == "provided"
    assert caps.source("streaming") == "native"
    assert caps.source("mcp") == "none"


def test_with_provided_does_not_mutate_the_original():
    original = RuntimeCapabilities()
    original.with_provided("memory")
    assert original.memory is False and original.provided == frozenset()


def test_unknown_provided_capability_is_rejected():
    with pytest.raises(ValueError, match="unknown provided capabilities: teleport"):
        RuntimeCapabilities(provided=frozenset({"teleport"}))


def test_guarantee_ids_are_unique_and_statuses_consistent():
    ids = [g.id for g in UNIFORM_GUARANTEES]
    assert len(ids) == len(set(ids))
    assert {g.status for g in UNIFORM_GUARANTEES} == {"covered", "planned"}
    assert len(guarantees_by_status("covered")) + len(guarantees_by_status("planned")) == len(ids)


def test_guarantee_validates_its_status_fields():
    with pytest.raises(ValueError, match="conformance tests"):
        Guarantee("x", "x", "covered")
    with pytest.raises(ValueError, match="plan phase"):
        Guarantee("x", "x", "planned")


def test_every_covered_guarantee_names_an_existing_conformance_case():
    tree = ast.parse(CONFORMANCE.read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    for guarantee in guarantees_by_status("covered"):
        missing = set(guarantee.tests) - defined
        assert not missing, f"{guarantee.id}: conformance cases missing: {sorted(missing)}"


def test_every_conformance_case_is_claimed_by_a_guarantee():
    tree = ast.parse(CONFORMANCE.read_text(encoding="utf-8"))
    cases = {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and node.name.startswith("test_")
    }
    claimed = {t for g in UNIFORM_GUARANTEES for t in g.tests}
    assert cases <= claimed, f"unclaimed conformance cases: {sorted(cases - claimed)}"
