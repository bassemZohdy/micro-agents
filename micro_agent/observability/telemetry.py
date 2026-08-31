"""Micro-Agent Observability — structured logging, metrics, and tracing.

Prefer OpenTelemetry-compatible instrumentation.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|credential|cookie)",
    re.IGNORECASE,
)
_REDACTED = "[REDACTED]"


def redact_mapping(value: Any, known_secrets: set[str] | None = None) -> Any:
    """Recursively redact sensitive keys and known secret values from a structure."""
    known = known_secrets or set()

    def _redact(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(k, str) and _SENSITIVE_KEY_PATTERN.search(k):
                    out[k] = _REDACTED
                else:
                    out[k] = _redact(v)
            return out
        if isinstance(node, (list, tuple)):
            return [_redact(item) for item in node]
        if isinstance(node, str) and node in known:
            return _REDACTED
        return node

    return _redact(value)


# ---------------------------------------------------------------------------
# Structured Logger
# ---------------------------------------------------------------------------


class StructuredLogger:
    """Structured JSON logger for Micro-Agent operations.

    Sensitive keys (api_key, authorization, ...) are redacted. Call
    register_secret() to also redact specific known secret values.
    """

    def __init__(self, name: str = "micro_agent") -> None:
        self._logger = logging.getLogger(name)
        self._context: dict[str, Any] = {}
        self._known_secrets: set[str] = set()

    def set_context(self, **kwargs: Any) -> None:
        """Set persistent context for all log entries."""
        self._context.update(kwargs)

    def set_level(self, level: str) -> None:
        """Set the minimum severity for emitted log entries.

        Unknown levels fall back to ``INFO``; configuration validation rejects
        invalid levels before this is called from the bootstrap.
        """
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    def register_secret(self, value: str) -> None:
        """Register a secret value to be redacted from all log entries."""
        if value:
            self._known_secrets.add(value)

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        entry = redact_mapping(
            {
                "level": level,
                "message": message,
                **self._context,
                **kwargs,
            },
            self._known_secrets,
        )
        self._logger.log(
            getattr(logging, level.upper(), logging.INFO),
            json.dumps(entry, default=str),
        )

    def info(self, message: str, **kwargs: Any) -> None:
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._log("ERROR", message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log("DEBUG", message, **kwargs)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class MetricPoint:
    """A single metric data point."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Simple in-memory metrics collector."""

    def __init__(self) -> None:
        self._metrics: list[MetricPoint] = []

    def record(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record a metric."""
        self._metrics.append(MetricPoint(name=name, value=value, labels=labels or {}))

    def increment(self, name: str, labels: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        self.record(name, 1.0, labels)

    def get_metrics(self, name: str | None = None) -> list[MetricPoint]:
        """Get recorded metrics, optionally filtered by name."""
        if name is None:
            return list(self._metrics)
        return [m for m in self._metrics if m.name == name]

    def clear(self) -> None:
        """Clear all recorded metrics."""
        self._metrics.clear()


# ---------------------------------------------------------------------------
# Trace Span
# ---------------------------------------------------------------------------


@dataclass
class TraceSpan:
    """A trace span for distributed tracing."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append({"name": name, "attributes": attributes or {}})

    def finish(self) -> None:
        self.end_time = time.time()

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000


# ---------------------------------------------------------------------------
# Telemetry facade
# ---------------------------------------------------------------------------


class Telemetry:
    """Facade bundling logger, metrics, and span recording.

    The invocation path uses this single object so agent/model/tool spans,
    metrics, and structured logs stay correlated. Spans are kept in memory;
    an OpenTelemetry exporter can replace the collector later without
    changing call sites.
    """

    def __init__(
        self,
        logger: StructuredLogger | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.logger = logger or StructuredLogger()
        self.metrics = metrics or MetricsCollector()
        self._spans: list[TraceSpan] = []

    def start_span(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceSpan:
        span = TraceSpan(
            trace_id=trace_id or str(uuid4()),
            span_id=str(uuid4())[:8],
            parent_span_id=parent_span_id,
            name=name,
            attributes=attributes or {},
        )
        self._spans.append(span)
        return span

    def finish_span(self, span: TraceSpan) -> None:
        span.finish()

    def get_spans(self, trace_id: str | None = None) -> list[TraceSpan]:
        if trace_id is None:
            return list(self._spans)
        return [s for s in self._spans if s.trace_id == trace_id]

    def increment(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.metrics.increment(name, labels)

    def record(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.metrics.record(name, value, labels)
