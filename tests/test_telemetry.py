"""Tests for Micro-Agent Observability — logging, metrics, tracing."""

import pytest

from micro_agent.observability import (
    MetricPoint,
    MetricsCollector,
    StructuredLogger,
    Telemetry,
    TraceSpan,
)


class TestStructuredLogger:
    """Test structured logger."""

    def test_logger_creation(self):
        logger = StructuredLogger("test")
        assert logger._context == {}

    def test_set_context(self):
        logger = StructuredLogger("test")
        logger.set_context(agent_id="a1", agent_version="1.0")
        assert logger._context["agent_id"] == "a1"

    def test_log_includes_context(self, caplog):
        logger = StructuredLogger("test_ctx")
        logger.set_context(agent_id="a1")
        with caplog.at_level("INFO", logger="test_ctx"):
            logger.info("test message", invocation_id="inv-1")
        assert any("test message" in r.message for r in caplog.records)


class TestMetricsCollector:
    """Test metrics collector."""

    def test_record_metric(self):
        collector = MetricsCollector()
        collector.record("invocation_count", 1.0)
        metrics = collector.get_metrics()
        assert len(metrics) == 1
        assert metrics[0].name == "invocation_count"

    def test_record_with_labels(self):
        collector = MetricsCollector()
        collector.record("latency", 150.5, {"endpoint": "/v1/invoke"})
        metrics = collector.get_metrics("latency")
        assert metrics[0].labels["endpoint"] == "/v1/invoke"

    def test_increment(self):
        collector = MetricsCollector()
        collector.increment("errors")
        collector.increment("errors")
        metrics = collector.get_metrics("errors")
        assert len(metrics) == 2

    def test_filter_by_name(self):
        collector = MetricsCollector()
        collector.record("a", 1.0)
        collector.record("b", 2.0)
        assert len(collector.get_metrics("a")) == 1
        assert len(collector.get_metrics("b")) == 1

    def test_clear(self):
        collector = MetricsCollector()
        collector.record("a", 1.0)
        collector.clear()
        assert len(collector.get_metrics()) == 0

    def test_telemetry_bounds_label_cardinality(self):
        from micro_agent.observability import Telemetry

        telemetry = Telemetry(max_label_values=1)
        telemetry.increment("requests_total", {"tenant": "first"})
        telemetry.increment("requests_total", {"tenant": "second"})
        points = telemetry.metrics.get_metrics("requests_total")
        assert [point.labels["tenant"] for point in points] == ["first", "[OTHER]"]

    @pytest.mark.otel
    def test_otel_environment_is_opt_in(self, monkeypatch):
        monkeypatch.delenv("MICRO_AGENT_OTEL_ENABLED", raising=False)
        assert not Telemetry.from_environment().otel_enabled
        monkeypatch.setenv("MICRO_AGENT_OTEL_ENABLED", "true")
        monkeypatch.setenv("MICRO_AGENT_OTEL_MAX_LABEL_VALUES", "3")
        assert Telemetry.from_environment().otel_enabled


class TestMetricPoint:
    """Test metric point."""

    def test_creation(self):
        point = MetricPoint(name="test", value=42.0)
        assert point.name == "test"
        assert point.value == 42.0
        assert point.labels == {}


class TestTraceSpan:
    """Test trace span."""

    def test_creation(self):
        span = TraceSpan(trace_id="t1", span_id="s1", name="agent.invoke")
        assert span.trace_id == "t1"
        assert span.span_id == "s1"
        assert span.end_time is None

    def test_set_attribute(self):
        span = TraceSpan(trace_id="t1", span_id="s1")
        span.set_attribute("agent.name", "test")
        assert span.attributes["agent.name"] == "test"

    def test_add_event(self):
        span = TraceSpan(trace_id="t1", span_id="s1")
        span.add_event("model.call", {"model": "gpt-4"})
        assert len(span.events) == 1
        assert span.events[0]["name"] == "model.call"

    def test_finish(self):
        span = TraceSpan(trace_id="t1", span_id="s1")
        assert span.duration_ms is None
        span.finish()
        assert span.end_time is not None
        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_parent_span(self):
        span = TraceSpan(trace_id="t1", span_id="s2", parent_span_id="s1", name="model.call")
        assert span.parent_span_id == "s1"


@pytest.mark.otel
def test_otel_bridge_exports_spans_metrics_and_w3c_context():
    """The optional SDK receives standard telemetry without changing the facade."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import context, propagate, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = Telemetry(
        otel_tracer=tracer_provider.get_tracer("micro-agent-test"),
        otel_meter=meter_provider.get_meter("micro-agent-test"),
        capture_content=False,
    )

    carrier: dict[str, str] = {}
    incoming = tracer_provider.get_tracer("parent").start_span("incoming")
    incoming_token = context.attach(trace.set_span_in_context(incoming))
    try:
        propagate.inject(carrier)
    finally:
        context.detach(incoming_token)
    incoming.end()
    token = telemetry.attach_context(carrier)
    span = telemetry.start_span(
        "agent.invoke",
        attributes={"content": "must not be exported", "agent": "test"},
    )
    span.add_event("agent.response", {"content": "also omitted"})
    telemetry.increment("agent_invocations_total", {"agent": "test"})
    telemetry.record("agent_invocation_latency_ms", 12.5, {"agent": "test"})
    telemetry.finish_span(span)
    telemetry.detach_context(token)

    exported = span_exporter.get_finished_spans()
    agent_spans = [item for item in exported if item.name == "agent.invoke"]
    assert len(agent_spans) == 1
    assert agent_spans[0].parent is not None
    assert agent_spans[0].parent.is_valid
    assert "content" not in agent_spans[0].attributes
    assert agent_spans[0].events[0].name == "agent.response"
    assert "content" not in agent_spans[0].events[0].attributes
    metric_data = metric_reader.get_metrics_data()
    assert metric_data is not None
    assert metric_data.resource_metrics
