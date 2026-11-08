"""GovernedModelProvider: deadline, telemetry, streaming and usage at the call boundary."""

from __future__ import annotations

import asyncio

import pytest

from micro_agent.governance import (
    GovernedModelProvider,
    InvocationDeadline,
    ModelCallScope,
    add_usage,
    current_model_scope,
    use_model_scope,
)
from micro_agent.models import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ModelStreamEvent,
    ProviderCapabilities,
)
from micro_agent.observability import Telemetry

CONFIG = ModelConfig(ref="m")
USAGE = {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}


class _Provider(ModelProvider):
    def __init__(self, *, streaming: bool = False, delay: float = 0.0, fail: bool = False) -> None:
        self.streaming, self.delay, self.fail = streaming, delay, fail
        self.generate_calls = self.stream_calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=self.streaming)

    async def generate(self, config, messages, tools=None):
        self.generate_calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionError("boom")
        return ModelResponse(content="hello", usage=dict(USAGE))

    async def stream(self, config, messages, tools=None):
        self.stream_calls += 1
        await asyncio.sleep(self.delay)
        yield ModelStreamEvent(delta="he")
        yield ModelStreamEvent(delta="llo")
        if not self.fail:
            yield ModelStreamEvent(response=ModelResponse(content="hello", usage=dict(USAGE)))

    async def health_check(self) -> bool:
        return True


def _governed(provider: _Provider, telemetry: Telemetry | None = None) -> GovernedModelProvider:
    telemetry = telemetry or Telemetry()
    return GovernedModelProvider(provider, lambda: telemetry)


def test_usage_helper_adds_in_place():
    total = {"a": 1}
    assert add_usage(total, {"a": 2, "b": 3}) is total
    assert total == {"a": 3, "b": 3}


@pytest.mark.asyncio
async def test_without_a_bound_scope_it_is_a_transparent_wrapper():
    inner = _Provider(streaming=True)
    governed = _governed(inner)
    assert governed.inner is inner and governed.capabilities().streaming is True
    assert await governed.health_check() is True
    assert current_model_scope() is None
    response = await governed.generate(CONFIG, [])
    assert response.content == "hello"
    deltas = [e.delta async for e in governed.stream(CONFIG, [])]
    assert deltas == ["he", "llo", ""]


@pytest.mark.asyncio
async def test_a_scoped_call_records_latency_usage_and_accumulates():
    telemetry = Telemetry()
    governed = _governed(_Provider(), telemetry)
    scope = ModelCallScope(trace_id="t", labels={"agent": "a"})
    with use_model_scope(scope):
        await governed.generate(CONFIG, [])
        await governed.generate(CONFIG, [])
    assert scope.usage == {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}
    assert scope.calls == 2
    assert telemetry.metrics.get_metrics("model_latency_ms")
    assert telemetry.metrics.get_metrics("model_tokens_total")
    assert current_model_scope() is None  # the binding ends with the block


@pytest.mark.asyncio
async def test_the_invocation_deadline_bounds_a_call():
    governed = _governed(_Provider(delay=5))
    with (
        use_model_scope(ModelCallScope(deadline=InvocationDeadline.from_seconds(0.1))),
        pytest.raises(TimeoutError),
    ):
        await governed.generate(CONFIG, [])


@pytest.mark.asyncio
async def test_the_model_timeout_caps_a_call_with_and_without_a_deadline():
    zero = ModelConfig(ref="m", timeout_seconds=0)
    with (
        use_model_scope(ModelCallScope(deadline=InvocationDeadline(None))),
        pytest.raises(TimeoutError),
    ):
        await _governed(_Provider(delay=1)).generate(zero, [])
    with use_model_scope(ModelCallScope()), pytest.raises(TimeoutError):
        await _governed(_Provider(delay=1)).generate(zero, [])


@pytest.mark.asyncio
async def test_failures_close_the_span_and_propagate():
    governed = _governed(_Provider(fail=True))
    with use_model_scope(ModelCallScope()), pytest.raises(ConnectionError):
        await governed.generate(CONFIG, [])


@pytest.mark.asyncio
async def test_a_scoped_delta_sink_receives_streamed_text():
    received: list[str] = []

    async def sink(delta: str) -> None:
        received.append(delta)

    inner = _Provider(streaming=True)
    scope = ModelCallScope(emit_delta=sink)
    with use_model_scope(scope):
        response = await _governed(inner).generate(CONFIG, [])
    assert received == ["he", "llo"] and response.content == "hello"
    assert inner.stream_calls == 1 and inner.generate_calls == 0
    assert scope.usage == USAGE


@pytest.mark.asyncio
async def test_a_stream_without_a_final_response_is_an_error():
    inner = _Provider(streaming=True, fail=True)  # ends without a final response
    scope = ModelCallScope(emit_delta=lambda delta: asyncio.sleep(0))
    with use_model_scope(scope), pytest.raises(RuntimeError, match="without a final response"):
        await _governed(inner).generate(CONFIG, [])
    with (
        use_model_scope(ModelCallScope()),
        pytest.raises(RuntimeError, match="without a final response"),
    ):
        _ = [event async for event in _governed(inner).stream(CONFIG, [])]


@pytest.mark.asyncio
async def test_scoped_streaming_is_bounded_and_accounted():
    governed = _governed(_Provider(streaming=True, delay=5))
    with (
        use_model_scope(ModelCallScope(deadline=InvocationDeadline.from_seconds(0.1))),
        pytest.raises(TimeoutError, match="deadline"),
    ):
        _ = [event async for event in governed.stream(CONFIG, [])]
    fast = ModelCallScope()
    with use_model_scope(fast):
        events = [event async for event in _governed(_Provider(streaming=True)).stream(CONFIG, [])]
    assert events[-1].response is not None and fast.usage == USAGE
