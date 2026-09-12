"""Durable, redacted audit events for security-relevant decisions.

Audit events record policy decisions (tool/side-effect/skill/model/MCP
denials, approval decisions) and authentication failures. Selection is
external configuration: the default ``stdout`` sink writes JSON lines for
platform collection (12-factor; durable via the deployment's log pipeline);
``file`` appends JSON lines to a configured path; ``none`` disables auditing.
Sensitive keys are redacted at write time — event fields carry identifiers
and reasons, never payloads or credentials.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TextIO

from micro_agent.observability.telemetry import redact_mapping


class AuditSink(ABC):
    """Receives security-relevant audit events."""

    @abstractmethod
    def record(self, event: str, **fields: Any) -> None:
        """Persist one audit event; implementations must redact fields."""


class JsonlAuditSink(AuditSink):
    """Appends redacted JSON-line events to a stream (stdout by default).

    One JSON object per line keeps the output ingestible by log pipelines
    and tail-able during development.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def record(self, event: str, **fields: Any) -> None:
        entry = {"ts": round(time.time(), 3), "event": event, **redact_mapping(dict(fields))}
        self._stream.write(json.dumps(entry, default=str) + "\n")
        self._stream.flush()


class FileAuditSink(JsonlAuditSink):
    """Appends redacted JSON-line events to a file."""

    def __init__(self, path: str) -> None:
        # Line-buffered append; the deployment owns rotation.
        self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115 - closed with sink
        super().__init__(self._file)

    def close(self) -> None:
        self._file.close()


class NullAuditSink(AuditSink):
    """Drops events; used when auditing is explicitly disabled."""

    def record(self, event: str, **fields: Any) -> None:
        return None


class SqliteAuditSink(AuditSink):
    """Tenant-aware SQLite audit sink with bounded retention.

    Audit fields are redacted before persistence.  The sink deliberately keeps
    the query surface small; deployments can export the append-only table to
    their SIEM while the retention window prevents unbounded local growth.
    """

    def __init__(self, path: str | Path, *, retention_seconds: float = 30 * 24 * 60 * 60) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be greater than zero")
        self.path = str(path)
        self.retention_seconds = float(retention_seconds)
        self._lock = threading.RLock()
        path_obj = Path(self.path)
        if path_obj.parent != Path(""):
            path_obj.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event TEXT NOT NULL,
                tenant_id TEXT,
                fields_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events (timestamp)"
        )
        self._connection.commit()

    def record(self, event: str, **fields: Any) -> None:
        now = time.time()
        redacted = redact_mapping(dict(fields))
        tenant_id = redacted.get("tenant_id") if isinstance(redacted, dict) else None
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO audit_events
                    (timestamp, event, tenant_id, fields_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now,
                    event,
                    str(tenant_id) if tenant_id is not None else None,
                    json.dumps(redacted, default=str),
                ),
            )
            self._connection.execute(
                "DELETE FROM audit_events WHERE timestamp < ?",
                (now - self.retention_seconds,),
            )
            self._connection.commit()

    def events(self, *, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Read recent redacted events for diagnostics and export jobs."""
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        with self._lock:
            if tenant_id is None:
                rows = self._connection.execute(
                    """
                    SELECT timestamp, event, tenant_id, fields_json
                    FROM audit_events ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT timestamp, event, tenant_id, fields_json
                    FROM audit_events
                    WHERE tenant_id = ? ORDER BY id DESC LIMIT ?
                    """,
                    (tenant_id, limit),
                ).fetchall()
        return [
            {
                "ts": row[0],
                "event": row[1],
                "tenant_id": row[2],
                **json.loads(row[3]),
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


__all__ = ["AuditSink", "FileAuditSink", "JsonlAuditSink", "NullAuditSink", "SqliteAuditSink"]
