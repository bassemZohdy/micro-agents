"""Micro-Agent Observability — structured logging, metrics, and tracing.

Prefer OpenTelemetry-compatible instrumentation.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Structured Logger
# ---------------------------------------------------------------------------


class StructuredLogger:
    """Structured JSON logger for Micro-Agent operations."""

    def __init__(self, name: str = "micro_agent") -> None:
        self._logger = logging.getLogger(name)
        self._context: dict[str, Any] = {}

    def set_context(self, **kwargs: Any) -> None:
        """Set persistent context for all log entries."""
        self._context.update(kwargs)

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        entry = {
            "level": level,
            "message": message,
            **self._context,
            **kwargs,
        }
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
