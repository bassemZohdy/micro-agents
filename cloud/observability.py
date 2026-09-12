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
store remains the lightweight default; ``SqliteObservabilityStore`` adds
retention-aware restart-safe storage for the reference deployment.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from cloud.auth import PlaneAuthenticator, install_plane_auth
from cloud.schemas import ObservabilityBatchContract

_MAX_EVENTS_PER_BATCH = 1000
_MAX_AUDIT_LIMIT = 1000
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
        """Validate and aggregate a batch; returns the accepted count.

        The batch is validated and normalized in full before anything is
        stored, so a rejected batch never leaves partial data behind.
        """
        if len(events) > _MAX_EVENTS_PER_BATCH:
            raise ValueError(f"batch exceeds {_MAX_EVENTS_PER_BATCH} events")
        spans: list[TraceSpan] = []
        usage: list[UsageRecord] = []
        audit: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("event must be an object")
            kind = event.get("kind")
            if kind not in _EVENT_KINDS:
                raise ValueError(f"event kind must be one of {sorted(_EVENT_KINDS)}")
            missing = _REQUIRED - set(event) - {"kind"}
            if missing:
                raise ValueError(f"event missing fields: {sorted(missing)}")
            tenant = event.get("tenant")
            if kind == "span":
                spans.append(
                    TraceSpan(
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
                )
            elif kind == "usage":
                usage.append(
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
                audit.append(
                    {
                        "trace_id": str(event["trace_id"]),
                        "agent": str(event["agent"]),
                        "tenant": str(tenant) if tenant else None,
                        "action": str(event.get("action", "")),
                        "decision": str(event.get("decision", "")),
                        "received_at": time.time(),
                    }
                )
        async with self._lock:
            for span in spans:
                self._spans.setdefault(span.trace_id, []).append(span)
            self._usage.extend(usage)
            self._audit.extend(audit)
        return len(events)

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
        if not 1 <= limit <= _MAX_AUDIT_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_AUDIT_LIMIT}")
        async with self._lock:
            events = list(self._audit)
        if tenant is not None:
            events = [event for event in events if event.get("tenant") == tenant]
        return list(reversed(events))[:limit]


class SqliteObservabilityStore:
    """Durable observability aggregation with bounded retention.

    The input contract and query shapes match :class:`InMemoryObservabilityStore`.
    SQLite stores normalized span, usage, and audit rows, purging records older
    than ``retention_seconds`` and evicting oldest rows when a table reaches its
    configured bound.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        retention_seconds: float = 30 * 24 * 60 * 60,
        max_records: int = 100_000,
    ) -> None:
        if retention_seconds <= 0 or max_records < 1:
            raise ValueError("retention_seconds and max_records must be positive")
        self.path = str(path)
        self.retention_seconds = float(retention_seconds)
        self.max_records = max_records
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(self.path)
            self._initialize(self._memory_connection)
        else:
            path_obj = Path(self.path)
            if path_obj.parent != Path(""):
                path_obj.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                self._initialize(connection)

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cloud_observability_spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                span_id TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_span_id TEXT,
                caller_agent TEXT,
                tenant TEXT,
                duration_ms REAL NOT NULL,
                status TEXT NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_obs_spans_trace
                ON cloud_observability_spans (trace_id, received_at, id);
            CREATE TABLE IF NOT EXISTS cloud_observability_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                tenant TEXT,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_obs_usage_filter
                ON cloud_observability_usage (agent, tenant, received_at);
            CREATE TABLE IF NOT EXISTS cloud_observability_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                tenant TEXT,
                action TEXT NOT NULL,
                decision TEXT NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_obs_audit_filter
                ON cloud_observability_audit (tenant, received_at, id);
            """
        )
        connection.commit()

    def _run(self, operation: Any) -> Any:
        with self._lock:
            if self._memory_connection is not None:
                return operation(self._memory_connection)
            with sqlite3.connect(self.path) as connection:
                return operation(connection)

    @staticmethod
    def _validated(events: list[dict[str, Any]]) -> InMemoryObservabilityStore:
        """Reuse the in-memory validator/normalizer without persisting it."""
        return InMemoryObservabilityStore()

    async def ingest(self, events: list[dict[str, Any]]) -> int:
        validator = self._validated(events)
        accepted = await validator.ingest(events)
        spans = [span for values in validator._spans.values() for span in values]
        usage = list(validator._usage)
        audit = list(validator._audit)
        cutoff = time.time() - self.retention_seconds

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            for span in spans:
                connection.execute(
                    "INSERT INTO cloud_observability_spans "
                    "(trace_id, agent, span_id, name, parent_span_id, caller_agent, tenant, "
                    "duration_ms, status, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        span.trace_id,
                        span.agent,
                        span.span_id,
                        span.name,
                        span.parent_span_id,
                        span.caller_agent,
                        span.tenant,
                        span.duration_ms,
                        span.status,
                        span.received_at,
                    ),
                )
            for record in usage:
                connection.execute(
                    "INSERT INTO cloud_observability_usage "
                    "(trace_id, agent, tenant, input_tokens, output_tokens, cost_usd, received_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.trace_id,
                        record.agent,
                        record.tenant,
                        record.input_tokens,
                        record.output_tokens,
                        record.cost_usd,
                        record.received_at,
                    ),
                )
            for event in audit:
                connection.execute(
                    "INSERT INTO cloud_observability_audit "
                    "(trace_id, agent, tenant, action, decision, received_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event["trace_id"],
                        event["agent"],
                        event["tenant"],
                        event["action"],
                        event["decision"],
                        event["received_at"],
                    ),
                )
            for table in (
                "cloud_observability_spans",
                "cloud_observability_usage",
                "cloud_observability_audit",
            ):
                connection.execute(f"DELETE FROM {table} WHERE received_at < ?", (cutoff,))
                connection.execute(
                    f"DELETE FROM {table} WHERE id IN ("
                    f"SELECT id FROM {table} ORDER BY received_at ASC, id ASC "
                    "LIMIT MAX(0, (SELECT COUNT(*) FROM "
                    f"{table}) - ?))",
                    (self.max_records,),
                )
            connection.commit()

        await asyncio.to_thread(self._run, operation)
        return accepted

    async def trace(self, trace_id: str) -> list[TraceSpan]:
        def operation(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
            return connection.execute(
                "SELECT trace_id, agent, span_id, name, parent_span_id, caller_agent, tenant, "
                "duration_ms, status, received_at FROM cloud_observability_spans "
                "WHERE trace_id = ? ORDER BY received_at, id",
                (trace_id,),
            ).fetchall()

        return [TraceSpan(*row) for row in await asyncio.to_thread(self._run, operation)]

    async def topology(self) -> list[dict[str, Any]]:
        def operation(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
            return connection.execute(
                "SELECT caller_agent, agent, COUNT(*) FROM cloud_observability_spans "
                "WHERE caller_agent IS NOT NULL AND caller_agent != agent "
                "GROUP BY caller_agent, agent ORDER BY caller_agent, agent"
            ).fetchall()

        return [
            {"caller_agent": str(row[0]), "callee_agent": str(row[1]), "calls": int(row[2])}
            for row in await asyncio.to_thread(self._run, operation)
        ]

    async def costs(self, *, tenant: str | None = None, agent: str | None = None) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if tenant is not None:
            clauses.append("tenant = ?")
            params.append(tenant)
        if agent is not None:
            clauses.append("agent = ?")
            params.append(agent)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
            return connection.execute(
                "SELECT agent, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
                "COALESCE(SUM(cost_usd), 0.0) FROM cloud_observability_usage"
                + where
                + " GROUP BY agent ORDER BY agent",
                params,
            ).fetchall()

        rows = await asyncio.to_thread(self._run, operation)
        totals: dict[str, dict[str, float]] = {
            str(row[0]): {
                "input_tokens": int(row[1]),
                "output_tokens": int(row[2]),
                "cost_usd": round(float(row[3]), 6),
            }
            for row in rows
        }
        return {
            "totals": {
                "input_tokens": sum(int(bucket["input_tokens"]) for bucket in totals.values()),
                "output_tokens": sum(int(bucket["output_tokens"]) for bucket in totals.values()),
                "cost_usd": round(sum(bucket["cost_usd"] for bucket in totals.values()), 6),
            },
            "by_agent": totals,
        }

    async def audit_events(
        self, *, tenant: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= _MAX_AUDIT_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_AUDIT_LIMIT}")

        def operation(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
            if tenant is None:
                return connection.execute(
                    "SELECT trace_id, agent, tenant, action, decision, received_at "
                    "FROM cloud_observability_audit ORDER BY received_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return connection.execute(
                "SELECT trace_id, agent, tenant, action, decision, received_at "
                "FROM cloud_observability_audit WHERE tenant = ? "
                "ORDER BY received_at DESC, id DESC LIMIT ?",
                (tenant, limit),
            ).fetchall()

        return [
            {
                "trace_id": str(row[0]),
                "agent": str(row[1]),
                "tenant": str(row[2]) if row[2] is not None else None,
                "action": str(row[3]),
                "decision": str(row[4]),
                "received_at": float(row[5]),
            }
            for row in await asyncio.to_thread(self._run, operation)
        ]

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(
                self._run, lambda connection: connection.execute("SELECT 1").fetchone()
            )
            return True
        except sqlite3.Error:
            return False

    async def close(self) -> None:
        if self._memory_connection is not None:
            await asyncio.to_thread(self._memory_connection.close)
            self._memory_connection = None


def create_observability_app(
    store: InMemoryObservabilityStore | SqliteObservabilityStore | None = None,
    *,
    database_path: str | Path | None = None,
    authenticator: PlaneAuthenticator | None = None,
) -> FastAPI:
    """Create the observability API with an in-memory or durable store."""
    if store is not None and database_path is not None:
        raise ValueError("pass store or database_path, not both")
    app = FastAPI(title="Micro-Agent Cloud Observability", version="0.1.0")
    owned = database_path is not None
    obs = (
        store
        if store is not None
        else (
            SqliteObservabilityStore(database_path)
            if database_path is not None
            else InMemoryObservabilityStore()
        )
    )
    app.state.observability_store = obs
    install_plane_auth(app, authenticator)

    @app.post("/observability/events")
    async def ingest_events(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            contract = ObservabilityBatchContract.model_validate(payload)
            events = [event.model_dump(exclude_none=True) for event in contract.events]
            accepted = await obs.ingest(events)
        except (ValueError, TypeError) as exc:
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
    async def get_audit(
        tenant: str | None = None,
        limit: int = Query(default=100, ge=1, le=_MAX_AUDIT_LIMIT),
    ) -> dict[str, Any]:
        return {"events": await obs.audit_events(tenant=tenant, limit=limit)}

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:
        health_check = getattr(obs, "health_check", None)
        healthy = bool(await health_check()) if health_check is not None else True
        if not healthy:
            raise HTTPException(status_code=503, detail="observability store unavailable")
        return {"ready": True}

    if owned:

        async def close_owned_store() -> None:
            close = getattr(obs, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

        app.router.on_shutdown.append(close_owned_store)

    return app


__all__ = [
    "InMemoryObservabilityStore",
    "SqliteObservabilityStore",
    "TraceSpan",
    "UsageRecord",
    "create_observability_app",
]
