"""Distributed observability aggregation for Micro-Agent Cloud (C4).

Agents write their own telemetry and audit locally (tamper-evident at the
source, per the C0 security model) and push events here for the cross-agent
view. The plane aggregates four things:

- **traces**: spans grouped by ``trace_id``, ordered, across agents;
- **topology**: caller→callee edges between agents, derived from spans
  carrying a ``caller_agent`` attribute;
- **cost/usage**: token counts and USD cost rolled up per agent and tenant;
- **audit**: an append-only, tenant-filterable view of emitted audit events.

The plane is read-mostly: it can answer what happened, never change it, and
losing it loses visibility, not agents (C0 failure stance). The in-memory
store is the minimal C4 form; a durable backend replaces, not extends, it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException

_MAX_EVENTS_PER_BATCH = 1000
_EVENT_KINDS = {"span", "usage", "audit"}
_REQUIRED = {"trace_id", "agent", "kind"}


@dataclass
class TraceSpan:
    """One span as aggregated across agents."""

    trace_id: str
    agent: str
    span_id: str
    name: str
    parent_span_id: str | None
    caller_agent: str | None
    tenant: str | None
    duration_ms: float
    status: str
    received_at: float = field(default_factory=time.time)


@dataclass
class UsageRecord:
    trace_id: str
    agent: str
    tenant: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    received_at: float = field(default_factory=time.time)


class InMemoryObservabilityStore:
    """Aggregates pushed events into traces, topology, costs, and audit."""

    def __init__(self) -> None:
        self._spans: dict[str, list[TraceSpan]] = {}
        self._usage: list[UsageRecord] = []
        self._audit: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def ingest(self, events: list[dict[str, Any]]) -> int:
        """Validate and aggregate a batch; returns the accepted count."""
        if len(events) > _MAX_EVENTS_PER_BATCH:
            raise ValueError(f"batch exceeds {_MAX_EVENTS_PER_BATCH} events")
        accepted = 0
        async with self._lock:
            for event in events:
                kind = event.get("kind")
                if kind not in _EVENT_KINDS:
                    raise ValueError(f"event kind must be one of {sorted(_EVENT_KINDS)}")
                missing = _REQUIRED - set(event) - {"kind"}
                if missing:
                    raise ValueError(f"event missing fields: {sorted(missing)}")
                tenant = event.get("tenant")
                if kind == "span":
                    span = TraceSpan(
                        trace_id=str(event["trace_id"]),
                        agent=str(event["agent"]),
                        span_id=str(event.get("span_id", "")),
                        name=str(event.get("name", "")),
                        parent_span_id=(
                            str(event["parent_span_id"]) if event.get("parent_span_id") else None
                        ),
                        caller_agent=(
                            str(event["caller_agent"]) if event.get("caller_agent") else None
                        ),
                        tenant=str(tenant) if tenant else None,
                        duration_ms=float(event.get("duration_ms", 0.0)),
                        status=str(event.get("status", "ok")),
                    )
                    self._spans.setdefault(span.trace_id, []).append(span)
                elif kind == "usage":
                    self._usage.append(
                        UsageRecord(
                            trace_id=str(event["trace_id"]),
                            agent=str(event["agent"]),
                            tenant=str(tenant) if tenant else None,
                            input_tokens=int(event.get("input_tokens", 0)),
                            output_tokens=int(event.get("output_tokens", 0)),
                            cost_usd=float(event.get("cost_usd", 0.0)),
                        )
                    )
                else:
                    self._audit.append(
                        {
                            "trace_id": str(event["trace_id"]),
                            "agent": str(event["agent"]),
                            "tenant": str(tenant) if tenant else None,
                            "action": str(event.get("action", "")),
                            "decision": str(event.get("decision", "")),
                            "received_at": time.time(),
                        }
                    )
                accepted += 1
        return accepted

    async def trace(self, trace_id: str) -> list[TraceSpan]:
        async with self._lock:
            spans = list(self._spans.get(trace_id, []))
        spans.sort(key=lambda span: span.received_at)
        return spans

    async def topology(self) -> list[dict[str, Any]]:
        """Caller→callee edges between agents with call counts."""
        edges: dict[tuple[str, str], int] = {}
        async with self._lock:
            for spans in self._spans.values():
                for span in spans:
                    if span.caller_agent and span.caller_agent != span.agent:
                        key = (span.caller_agent, span.agent)
                        edges[key] = edges.get(key, 0) + 1
        return [
            {"caller_agent": caller, "callee_agent": callee, "calls": calls}
            for (caller, callee), calls in sorted(edges.items())
        ]

    async def costs(self, *, tenant: str | None = None, agent: str | None = None) -> dict[str, Any]:
        async with self._lock:
            records = [
                record
                for record in self._usage
                if (tenant is None or record.tenant == tenant)
                and (agent is None or record.agent == agent)
            ]
        totals: dict[str, dict[str, float]] = {}
        for record in records:
            bucket = totals.setdefault(
                record.agent, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            )
            bucket["input_tokens"] += record.input_tokens
            bucket["output_tokens"] += record.output_tokens
            bucket["cost_usd"] = round(bucket["cost_usd"] + record.cost_usd, 6)
        return {
            "totals": {
                "input_tokens": sum(b["input_tokens"] for b in totals.values()),
                "output_tokens": sum(b["output_tokens"] for b in totals.values()),
                "cost_usd": round(sum(b["cost_usd"] for b in totals.values()), 6),
            },
            "by_agent": totals,
        }

    async def audit_events(
        self, *, tenant: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with self._lock:
            events = list(self._audit)
        if tenant is not None:
            events = [event for event in events if event.get("tenant") == tenant]
        return list(reversed(events))[:limit]


def create_observability_app(
    store: InMemoryObservabilityStore | None = None,
) -> FastAPI:
    """FastAPI ingest + query surface (unauthenticated; C3 gateway work)."""
    app = FastAPI(title="Micro-Agent Cloud Observability", version="0.1.0")
    obs = store if store is not None else InMemoryObservabilityStore()
    app.state.observability_store = obs

    @app.post("/observability/events")
    async def ingest_events(payload: dict[str, Any]) -> dict[str, Any]:
        events = payload.get("events")
        if not isinstance(events, list):
            raise HTTPException(status_code=422, detail="payload must carry an events list")
        try:
            accepted = await obs.ingest(events)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"accepted": accepted}

    @app.get("/observability/traces/{trace_id}")
    async def get_trace(trace_id: str) -> dict[str, Any]:
        spans = await obs.trace(trace_id)
        if not spans:
            raise HTTPException(status_code=404, detail=f"unknown trace '{trace_id}'")
        return {
            "trace_id": trace_id,
            "spans": [
                {
                    "agent": span.agent,
                    "span_id": span.span_id,
                    "name": span.name,
                    "parent_span_id": span.parent_span_id,
                    "caller_agent": span.caller_agent,
                    "tenant": span.tenant,
                    "duration_ms": span.duration_ms,
                    "status": span.status,
                }
                for span in spans
            ],
        }

    @app.get("/observability/topology")
    async def get_topology() -> dict[str, Any]:
        return {"edges": await obs.topology()}

    @app.get("/observability/costs")
    async def get_costs(tenant: str | None = None, agent: str | None = None) -> dict[str, Any]:
        return await obs.costs(tenant=tenant, agent=agent)

    @app.get("/observability/audit")
    async def get_audit(tenant: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"events": await obs.audit_events(tenant=tenant, limit=limit)}

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:
        return {"ready": True}

    return app


__all__ = ["InMemoryObservabilityStore", "TraceSpan", "UsageRecord", "create_observability_app"]
