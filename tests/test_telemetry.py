"""Tests for Micro-Agent Observability — logging, metrics, tracing."""

from micro_agent.observability import (
    MetricPoint,
    MetricsCollector,
    StructuredLogger,
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
