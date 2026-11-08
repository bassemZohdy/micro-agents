"""Behaviors every runtime must exhibit identically, native or provided.

A Micro-Agent definition must behave the same whichever framework runs it.
This module is the single list of those guarantees. The conformance suite
(``tests/test_runtime_conformance.py``) is the executable form: a guarantee is
``covered`` once the named conformance cases exist, and ``planned`` while the
gap-filling layer that delivers it is still being built (see ``TODO.md``,
"Runtime gap-filling layer").

Guarantees describe observable behavior at Micro-Agent boundaries (tools,
model provider, stores, request/response). They never mention a framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GuaranteeStatus = Literal["covered", "planned"]


@dataclass(frozen=True)
class Guarantee:
    """One cross-runtime behavioral guarantee."""

    id: str
    summary: str
    status: GuaranteeStatus
    #: Conformance test functions that prove the guarantee (``covered`` only).
    tests: tuple[str, ...] = ()
    #: Plan phase in TODO.md that delivers a ``planned`` guarantee.
    phase: int | None = None

    def __post_init__(self) -> None:
        if self.status == "covered" and not self.tests:
            raise ValueError(f"covered guarantee '{self.id}' must name its conformance tests")
        if self.status == "planned" and self.phase is None:
            raise ValueError(f"planned guarantee '{self.id}' must name its plan phase")


UNIFORM_GUARANTEES: tuple[Guarantee, ...] = (
    Guarantee(
        "identity",
        "Creating an agent exposes Micro-Agent identity and capabilities, never framework types.",
        "covered",
        ("test_create_exposes_identity_without_framework_types",),
    ),
    Guarantee(
        "capability-report",
        "Capabilities are reported truthfully, including whether each is native or provided.",
        "covered",
        ("test_reports_a_capability_matrix",),
    ),
    Guarantee(
        "invocation",
        "A simple invocation returns the model content in the Micro-Agent response shape.",
        "covered",
        ("test_simple_invocation_returns_the_model_content",),
    ),
    Guarantee(
        "session-replay",
        "Session history is replayed to the model on the next invocation.",
        "covered",
        ("test_session_history_is_replayed_to_the_model",),
    ),
    Guarantee(
        "tool-loop",
        "Requested tools run and feed the next model turn, bounded by max iterations.",
        "covered",
        ("test_tool_loop_executes_requested_tools_then_answers",),
    ),
    Guarantee(
        "model-failure",
        "A model failure surfaces as an error response, subject to the error policy.",
        "covered",
        ("test_model_failure_surfaces_as_an_error",),
    ),
    Guarantee(
        "startup-health",
        "An unhealthy model dependency blocks startup.",
        "covered",
        ("test_unhealthy_model_blocks_startup",),
    ),
    Guarantee(
        "policy-deny",
        "A tool denied by policy is never executed and yields a denied result.",
        "planned",
        phase=2,
    ),
    Guarantee(
        "argument-validation",
        "Tool arguments are validated against the tool schema before execution.",
        "planned",
        phase=2,
    ),
    Guarantee(
        "idempotent-operations",
        "Side-effect operations run at most once per operation key, even after retry or resume.",
        "planned",
        phase=2,
    ),
    Guarantee(
        "approval-pause-resume",
        "A tool needing approval pauses the invocation durably and resumes after a decision.",
        "planned",
        phase=2,
    ),
    Guarantee(
        "audit-telemetry",
        "Tool and model calls emit the same audit and telemetry events.",
        "planned",
        phase=2,
    ),
    Guarantee(
        "checkpoint-resume",
        "An invocation resumes from a checkpoint at a tool-round boundary.",
        "planned",
        phase=3,
    ),
    Guarantee(
        "retry-fallback-breaker",
        "Model retry, fallback and circuit breaking behave identically.",
        "planned",
        phase=4,
    ),
    Guarantee(
        "deadline",
        "The invocation deadline bounds model and tool calls.",
        "planned",
        phase=4,
    ),
    Guarantee(
        "session-portability",
        "A stored session written under one runtime is readable under another.",
        "planned",
        phase=4,
    ),
)


def guarantees_by_status(status: GuaranteeStatus) -> tuple[Guarantee, ...]:
    """Return the guarantees in one status, in declaration order."""
    return tuple(g for g in UNIFORM_GUARANTEES if g.status == status)
