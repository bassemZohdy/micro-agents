"""A stored session written under one runtime is readable under every other one.

Operators change ``MICRO_AGENT_RUNTIME`` between deployments; the conversation
must survive it. Each ordered pair of available runtimes shares one session
provider: the first writes a turn that includes a tool round, the second must
see that history and the stored layout must be the Micro-Agent transcript.
"""

from __future__ import annotations

import itertools
import json

import pytest

from micro_agent.core import AgentRequest
from micro_agent.models import FakeModelConfig, FakeModelProvider, ModelResponse
from micro_agent.session import InMemorySessionProvider
from tests.test_runtime_conformance import (
    _ChargeTool,
    _custom,
    _definition,
    _google_adk,
    _importable,
    _langgraph,
    _RequestsThenAnswers,
)

_FACTORIES = {"custom": (_custom, "micro_agent")}
if _importable("google.adk"):
    _FACTORIES["google-adk"] = (_google_adk, "google.adk")
if _importable("langgraph.graph"):
    _FACTORIES["langgraph"] = (_langgraph, "langgraph.graph")

_PAIRS = [pytest.param(a, b, id=f"{a}->{b}") for a, b in itertools.permutations(_FACTORIES, 2)] + [
    pytest.param(name, name, id=f"{name}->{name}") for name in _FACTORIES
]


class _Recorder(FakeModelProvider):
    def __init__(self) -> None:
        super().__init__(FakeModelConfig(response="second answer"))
        self.seen: list[list[dict]] = []

    async def generate(self, config, messages, tools=None):
        self.seen.append([dict(message) for message in messages])
        return ModelResponse(content="second answer")


async def _first_turn(factory, sessions) -> None:
    tool = _ChargeTool("lookup", side_effect="read_only")
    runtime = factory(
        _RequestsThenAnswers("lookup", "first answer"),
        session_provider=sessions,
        tool_registry={"lookup": tool},
    )
    agent = await runtime.create(_definition(tools=True, tool="lookup"))
    try:
        await runtime.start(agent)
        await runtime.invoke(
            agent, AgentRequest(input={"message": "remember-me"}, session_id="portable")
        )
    finally:
        await runtime.shutdown(agent)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("writer", "reader"), _PAIRS)
async def test_a_session_written_by_one_runtime_is_readable_by_another(writer, reader):
    sessions = InMemorySessionProvider()
    await _first_turn(_FACTORIES[writer][0], sessions)

    stored = await sessions.get("portable")
    assert stored is not None
    # One layout for everyone: transcript messages, never framework events.
    assert stored.messages and {m["role"] for m in stored.messages} <= {"user", "assistant", "tool"}
    assert all("content" in m for m in stored.messages)
    assert any(m.get("tool_calls") for m in stored.messages if m["role"] == "assistant")
    assert any(m["role"] == "tool" for m in stored.messages)

    recorder = _Recorder()
    runtime = _FACTORIES[reader][0](recorder, session_provider=sessions)
    agent = await runtime.create(_definition())
    try:
        await runtime.start(agent)
        await runtime.invoke(
            agent, AgentRequest(input={"message": "second-turn"}, session_id="portable")
        )
    finally:
        await runtime.shutdown(agent)
        await runtime.close()

    history = json.dumps(recorder.seen[0], default=str)
    assert "remember-me" in history
    assert "first answer" in history
    assert "lookup" in history  # the tool round survived the hand-over

    after = await sessions.get("portable")
    assert after is not None
    # The reader appended to the same transcript instead of starting a new one.
    assert len(after.messages) > len(stored.messages)
    assert "second answer" in json.dumps(after.messages, default=str)
