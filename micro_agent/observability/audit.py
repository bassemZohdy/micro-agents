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
import sys
import time
from abc import ABC, abstractmethod
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


__all__ = ["AuditSink", "FileAuditSink", "JsonlAuditSink", "NullAuditSink"]
