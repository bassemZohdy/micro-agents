"""Optional OpenTelemetry boundary integration tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.models import FakeModelConfig
from micro_agent.observability import Telemetry
from runtimes.adk import AdkRuntime, AdkRuntimeConfig

pytest.importorskip("opentelemetry.sdk")
pytestmark = pytest.mark.otel


def _definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "otel-test-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Return the requested response."},
                "dependencies": {"model": {"ref": "fake-model"}},
            },
        }
    )


@pytest.mark.asyncio
async def test_http_trace_context_reaches_runtime_and_response():
    from opentelemetry import context, propagate, trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry(otel_tracer=tracer_provider.get_tracer("otel-test"))
    runtime = AdkRuntime(
        AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(response="otel response"),
            telemetry=telemetry,
        )
    )
    agent = DefaultMicroAgent(_definition(), runtime)
    await agent.initialize()
    await agent.start()
    try:
        app = create_app(agent, telemetry=telemetry)
        carrier: dict[str, str] = {}
        incoming = tracer_provider.get_tracer("client").start_span("client.request")
        incoming_token = context.attach(trace.set_span_in_context(incoming))
        try:
            propagate.inject(carrier)
        finally:
            context.detach(incoming_token)
        incoming.end()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/invoke",
                headers=carrier,
                json={"input": {"message": "trace this"}},
            )
        assert response.status_code == 200
        assert response.json()["output"]["content"] == "otel response"
        assert response.headers.get("traceparent")

        spans = exporter.get_finished_spans()
        http_span = next(span for span in spans if span.name == "http.request")
        agent_span = next(span for span in spans if span.name == "agent.invoke")
        assert http_span.parent is not None and http_span.parent.is_valid
        assert agent_span.parent is not None
        assert agent_span.parent.span_id == http_span.context.span_id
    finally:
        await agent.stop()
        await agent.shutdown()
        await runtime.close()
