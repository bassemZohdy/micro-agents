"""Micro-Agent Observability — structured logging, metrics, and tracing.

Prefer OpenTelemetry-compatible instrumentation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Mapping
from contextlib import suppress
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
_CONTENT_ATTRIBUTE_KEYS = {
    "content",
    "input",
    "messages",
    "prompt",
    "request.body",
    "response",
    "response.body",
}


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
    _otel_span: Any | None = field(default=None, repr=False, compare=False)
    _otel_context: Any | None = field(default=None, repr=False, compare=False)
    _otel_context_token: Any | None = field(default=None, repr=False, compare=False)
    _otel_capture_content: bool = field(default=False, repr=False, compare=False)
    _otel_max_attribute_length: int = field(default=256, repr=False, compare=False)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value
        if self._otel_span is not None:
            with suppress(Exception):
                if self._otel_capture_content or key.lower() not in _CONTENT_ATTRIBUTE_KEYS:
                    self._otel_span.set_attribute(
                        key, _otel_value(value, self._otel_max_attribute_length)
                    )

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        event_attributes = attributes or {}
        self.events.append({"name": name, "attributes": event_attributes})
        if self._otel_span is not None:
            with suppress(Exception):
                self._otel_span.add_event(
                    name,
                    attributes={
                        key: _otel_value(value, self._otel_max_attribute_length)
                        for key, value in event_attributes.items()
                        if self._otel_capture_content or key.lower() not in _CONTENT_ATTRIBUTE_KEYS
                    },
                )

    def finish(self) -> None:
        if self.end_time is not None:
            return
        self.end_time = time.time()
        if self._otel_span is not None:
            with suppress(Exception):
                self._otel_span.end()
        if self._otel_context is not None and self._otel_context_token is not None:
            with suppress(Exception):
                self._otel_context.detach(self._otel_context_token)

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
    metrics, and structured logs stay correlated. The in-memory collectors
    remain available for deterministic tests. When the optional OpenTelemetry
    extra is installed and enabled, the same calls also emit standard spans
    and metrics and participate in W3C trace-context propagation.
    """

    def __init__(
        self,
        logger: StructuredLogger | None = None,
        metrics: MetricsCollector | None = None,
        *,
        otel_enabled: bool | None = None,
        service_name: str = "micro-agent",
        capture_content: bool = False,
        max_attribute_length: int = 256,
        max_label_values: int = 100,
        otel_tracer: Any | None = None,
        otel_meter: Any | None = None,
    ) -> None:
        if max_attribute_length < 1:
            raise ValueError("max_attribute_length must be greater than zero")
        if max_label_values < 1:
            raise ValueError("max_label_values must be greater than zero")
        self.logger = logger or StructuredLogger()
        self.metrics = metrics or MetricsCollector()
        self._spans: list[TraceSpan] = []
        self._capture_content = capture_content
        self._max_attribute_length = max_attribute_length
        self._max_label_values = max_label_values
        self._label_values: dict[tuple[str, str], set[str]] = {}
        self._otel_tracer = otel_tracer
        self._otel_meter = otel_meter
        self._otel_context: Any | None = None
        self._otel_propagate: Any | None = None
        self._otel_trace: Any | None = None
        self._otel_counters: dict[str, Any] = {}
        self._otel_histograms: dict[str, Any] = {}

        enabled = otel_enabled
        if enabled is None:
            enabled = otel_tracer is not None or otel_meter is not None
        if enabled and self._otel_tracer is None and self._otel_meter is None:
            try:
                from importlib import import_module

                context = import_module("opentelemetry.context")
                otel_metrics = import_module("opentelemetry.metrics")
                propagate = import_module("opentelemetry.propagate")
                trace = import_module("opentelemetry.trace")
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "OpenTelemetry is enabled but unavailable; install the optional "
                    "'otel' extra ('micro-agents[otel]')"
                ) from exc
            self._otel_tracer = trace.get_tracer(service_name)
            self._otel_meter = otel_metrics.get_meter(service_name)
            self._otel_context = context
            self._otel_propagate = propagate
            self._otel_trace = trace
        elif self._otel_tracer is not None or self._otel_meter is not None:
            try:
                from importlib import import_module

                context = import_module("opentelemetry.context")
                propagate = import_module("opentelemetry.propagate")
                trace = import_module("opentelemetry.trace")
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "an OpenTelemetry tracer or meter was supplied but the API is unavailable"
                ) from exc
            self._otel_context = context
            self._otel_propagate = propagate
            self._otel_trace = trace
        self._otel_enabled = self._otel_tracer is not None or self._otel_meter is not None

    @classmethod
    def from_environment(
        cls,
        logger: StructuredLogger | None = None,
        metrics: MetricsCollector | None = None,
    ) -> Telemetry:
        """Build telemetry from deployment-safe ``MICRO_AGENT_OTEL_*`` settings.

        OpenTelemetry is opt-in. Exporters/providers are configured through the
        standard OpenTelemetry SDK environment or process bootstrap; this
        facade only owns instrumentation, propagation, and safe defaults.
        """
        enabled = _parse_bool(os.environ.get("MICRO_AGENT_OTEL_ENABLED"), False)
        capture_content = _parse_bool(os.environ.get("MICRO_AGENT_OTEL_CAPTURE_CONTENT"), False)
        return cls(
            logger=logger,
            metrics=metrics,
            otel_enabled=enabled,
            service_name=(
                os.environ.get("MICRO_AGENT_OTEL_SERVICE_NAME")
                or os.environ.get("OTEL_SERVICE_NAME")
                or "micro-agent"
            ),
            capture_content=capture_content,
            max_attribute_length=_parse_positive_int(
                os.environ.get("MICRO_AGENT_OTEL_MAX_ATTRIBUTE_LENGTH"), 256
            ),
            max_label_values=_parse_positive_int(
                os.environ.get("MICRO_AGENT_OTEL_MAX_LABEL_VALUES"), 100
            ),
        )

    @property
    def otel_enabled(self) -> bool:
        """Whether standard OpenTelemetry instrumentation is active."""
        return self._otel_enabled

    def attach_context(self, headers: Mapping[str, str]) -> Any | None:
        """Attach W3C trace context extracted from an inbound carrier."""
        if not self._otel_enabled or self._otel_context is None or self._otel_propagate is None:
            return None
        try:
            return self._otel_context.attach(self._otel_propagate.extract(dict(headers)))
        except Exception:  # noqa: BLE001 — malformed context is ignored safely
            return None

    def detach_context(self, token: Any | None) -> None:
        """Detach a token returned by :meth:`attach_context`."""
        if token is None or self._otel_context is None:
            return
        with suppress(Exception):
            self._otel_context.detach(token)

    def inject_context(self, headers: Any) -> None:
        """Inject the current W3C trace context into an outbound carrier."""
        if not self._otel_enabled or self._otel_propagate is None:
            return
        with suppress(Exception):
            self._otel_propagate.inject(headers)

    def current_trace_id(self) -> str | None:
        """Return the active OpenTelemetry trace id as lowercase hex."""
        if not self._otel_enabled or self._otel_trace is None:
            return None
        try:
            span = self._otel_trace.get_current_span()
            context = span.get_span_context()
            if context.is_valid:
                return format(context.trace_id, "032x")
        except Exception:  # noqa: BLE001 — telemetry must never break work
            pass
        return None

    def start_span(
        self,
        name: str,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> TraceSpan:
        safe_attributes = dict(attributes or {})
        otel_span: Any | None = None
        otel_token: Any | None = None
        if self._otel_tracer is not None and self._otel_context is not None and self._otel_trace:
            try:
                otel_span = self._otel_tracer.start_span(
                    name,
                    attributes=self._otel_attributes(safe_attributes),
                )
                otel_token = self._otel_context.attach(
                    self._otel_trace.set_span_in_context(otel_span)
                )
            except Exception:  # noqa: BLE001 — telemetry must never break work
                otel_span = None
                otel_token = None
        span = TraceSpan(
            trace_id=trace_id or self.current_trace_id() or str(uuid4()),
            span_id=self._otel_span_id(otel_span) or str(uuid4())[:8],
            parent_span_id=parent_span_id,
            name=name,
            attributes=safe_attributes,
            _otel_span=otel_span,
            _otel_context=self._otel_context,
            _otel_context_token=otel_token,
            _otel_capture_content=self._capture_content,
            _otel_max_attribute_length=self._max_attribute_length,
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
        bounded = self._bounded_labels(name, labels)
        self.metrics.increment(name, bounded)
        counter = self._otel_instrument(name, counter=True)
        if counter is not None:
            with suppress(Exception):
                counter.add(1, attributes=bounded)

    def record(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        bounded = self._bounded_labels(name, labels)
        self.metrics.record(name, value, bounded)
        instrument = self._otel_instrument(name, counter=name.endswith("_total"))
        if instrument is not None:
            with suppress(Exception):
                if name.endswith("_total"):
                    instrument.add(value, attributes=bounded)
                else:
                    instrument.record(value, attributes=bounded)

    def _otel_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        redacted = redact_mapping(attributes)
        result: dict[str, Any] = {}
        for key, value in redacted.items():
            if not self._capture_content and key.lower() in _CONTENT_ATTRIBUTE_KEYS:
                continue
            result[key] = _otel_value(value, self._max_attribute_length)
        return result

    def _otel_span_id(self, span: Any | None) -> str | None:
        if span is None:
            return None
        try:
            context = span.get_span_context()
            if context.is_valid:
                return format(context.span_id, "016x")
        except Exception:  # noqa: BLE001 — telemetry must never break work
            pass
        return None

    def _bounded_labels(self, name: str, labels: dict[str, str] | None) -> dict[str, str]:
        bounded: dict[str, str] = {}
        for key, raw_value in (labels or {}).items():
            value = str(raw_value)[: self._max_attribute_length]
            seen = self._label_values.setdefault((name, key), set())
            if value not in seen and len(seen) >= self._max_label_values:
                value = "[OTHER]"
            else:
                seen.add(value)
            bounded[str(key)[: self._max_attribute_length]] = value
        return bounded

    def _otel_instrument(self, name: str, *, counter: bool) -> Any | None:
        if self._otel_meter is None:
            return None
        cache = self._otel_counters if counter else self._otel_histograms
        if name in cache:
            return cache[name]
        try:
            instrument = (
                self._otel_meter.create_counter(name, unit="1")
                if counter
                else self._otel_meter.create_histogram(name, unit="ms")
            )
        except Exception:  # noqa: BLE001 — telemetry must never break work
            return None
        cache[name] = instrument
        return instrument


def _parse_bool(value: str | None, default: bool) -> bool:
    """Parse a deployment boolean or fail clearly on an invalid value."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_positive_int(value: str | None, default: int) -> int:
    """Parse a positive deployment integer with a stable error."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"invalid positive integer value: {value!r}") from exc
    if parsed < 1:
        raise ValueError(f"value must be positive: {value!r}")
    return parsed


def _otel_value(value: Any, max_length: int = 256) -> Any:
    """Convert values to bounded OpenTelemetry attribute types."""
    if isinstance(value, str):
        return value[:max_length]
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_otel_value(item, max_length) for item in value[:32]]
    return str(value)[:max_length]
