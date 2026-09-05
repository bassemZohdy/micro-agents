"""Unit tests for the A2A server bridge."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from micro_agent.interoperability import a2a_server


class FakeTextPart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class FakePart:
    def __init__(self, *, root: object) -> None:
        self.root = root


class FakeAgentExecutor:
    pass


class FakeTaskUpdater:
    last: FakeTaskUpdater | None = None

    def __init__(self, event_queue: object, *, task_id: str, context_id: str) -> None:
        self.event_queue = event_queue
        self.task_id = task_id
        self.context_id = context_id
        self.events: list[tuple[str, Any]] = []
        type(self).last = self

    async def submit(self) -> None:
        self.events.append(("submit", None))

    async def start_work(self) -> None:
        self.events.append(("start_work", None))

    def new_agent_message(self, parts: list[FakePart]) -> list[FakePart]:
        return parts

    async def add_artifact(
        self,
        parts: list[FakePart],
        *,
        artifact_id: str | None = None,
        name: str | None = None,
        append: bool | None = None,
        last_chunk: bool | None = None,
    ) -> None:
        self.events.append(("artifact", (parts, name, artifact_id, append, last_chunk)))

    async def complete(self) -> None:
        self.events.append(("complete", None))

    async def failed(self, message: object) -> None:
        self.events.append(("failed", message))

    async def cancel(self) -> None:
        self.events.append(("cancel", None))


def _sdk() -> SimpleNamespace:
    return SimpleNamespace(
        AgentExecutor=FakeAgentExecutor,
        TaskUpdater=FakeTaskUpdater,
        Part=FakePart,
        TextPart=FakeTextPart,
    )


class FakeContext:
    def __init__(self, text: str, *, task_id: str | None = "task-1") -> None:
        self.task_id = task_id
        self.context_id = "context-1"
        self.text = text

    def get_user_input(self) -> str:
        return self.text


class FakeAgent:
    def __init__(
        self,
        output: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output = output or {"content": "done"}
        self.error = error
        self.request: object | None = None

    async def invoke(self, request: object) -> SimpleNamespace:
        self.request = request
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output=self.output)


class StreamingAgent(FakeAgent):
    @property
    def runtime_capabilities(self) -> SimpleNamespace:
        return SimpleNamespace(streaming=True)

    async def stream(self, request: object):
        self.request = request
        yield SimpleNamespace(delta="hel", response=None)
        yield SimpleNamespace(delta="lo", response=None)
        yield SimpleNamespace(response=SimpleNamespace(output={"content": "hello"}))


def test_payload_from_supports_json_and_plain_text() -> None:
    assert a2a_server._payload_from(FakeContext('{"question": "ping"}')) == {"question": "ping"}
    assert a2a_server._payload_from(FakeContext("ping")) == {"message": "ping"}
    assert a2a_server._payload_from(FakeContext("")) == {}


@pytest.mark.asyncio
async def test_executor_completes_and_maps_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a2a_server, "_import_sdk", _sdk)
    agent = FakeAgent()
    executor = a2a_server.build_micro_agent_executor(agent)  # type: ignore[arg-type]

    await executor.execute(FakeContext('{"question": "ping"}'), "queue")

    assert agent.request is not None
    assert agent.request.input == {"question": "ping"}  # type: ignore[attr-defined]
    assert FakeTaskUpdater.last is not None
    assert [name for name, _value in FakeTaskUpdater.last.events] == [
        "submit",
        "start_work",
        "artifact",
        "complete",
    ]


@pytest.mark.asyncio
async def test_executor_streams_appendable_artifact_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a2a_server, "_import_sdk", _sdk)
    agent = StreamingAgent()
    executor = a2a_server.build_micro_agent_executor(agent)  # type: ignore[arg-type]

    await executor.execute(FakeContext("ping"), "queue")

    assert FakeTaskUpdater.last is not None
    artifacts = [payload for name, payload in FakeTaskUpdater.last.events if name == "artifact"]
    assert len(artifacts) == 2
    first_parts, _, artifact_id, append, last_chunk = artifacts[0]
    assert first_parts[0].root.text == "hel"
    assert artifact_id == "task-1:result"
    assert append is False
    assert last_chunk is False
    final_parts, _, final_artifact_id, final_append, final_last_chunk = artifacts[1]
    assert final_parts[0].root.text == "lo"
    assert final_artifact_id == artifact_id
    assert final_append is True
    assert final_last_chunk is True
    assert [name for name, _value in FakeTaskUpdater.last.events] == [
        "submit",
        "start_work",
        "artifact",
        "artifact",
        "complete",
    ]


@pytest.mark.asyncio
async def test_executor_converts_invocation_failure_to_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(a2a_server, "_import_sdk", _sdk)
    executor = a2a_server.build_micro_agent_executor(
        FakeAgent(error=RuntimeError("boom"))  # type: ignore[arg-type]
    )

    await executor.execute(FakeContext("ping"), "queue")

    assert FakeTaskUpdater.last is not None
    assert [name for name, _value in FakeTaskUpdater.last.events] == [
        "submit",
        "start_work",
        "failed",
    ]


@pytest.mark.asyncio
async def test_executor_cancel_marks_task_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a2a_server, "_import_sdk", _sdk)
    executor = a2a_server.build_micro_agent_executor(FakeAgent())  # type: ignore[arg-type]

    await executor.cancel(FakeContext("ping"), "queue")

    assert FakeTaskUpdater.last is not None
    assert [name for name, _value in FakeTaskUpdater.last.events] == ["cancel"]


@pytest.mark.asyncio
async def test_executor_cancel_interrupts_in_flight_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(a2a_server, "_import_sdk", _sdk)
    started = asyncio.Event()
    never_finishes = asyncio.Event()

    class BlockingAgent(FakeAgent):
        async def invoke(self, request: object) -> SimpleNamespace:
            self.request = request
            started.set()
            await never_finishes.wait()
            return SimpleNamespace(output=self.output)

    agent = BlockingAgent()
    executor = a2a_server.build_micro_agent_executor(agent)  # type: ignore[arg-type]
    context = FakeContext("ping")
    execute_task = asyncio.create_task(executor.execute(context, "queue"))

    await asyncio.wait_for(started.wait(), timeout=1)
    await executor.cancel(context, "queue")

    with pytest.raises(asyncio.CancelledError):
        await execute_task
    assert FakeTaskUpdater.last is not None
    assert [name for name, _value in FakeTaskUpdater.last.events] == [
        "submit",
        "start_work",
        "cancel",
    ]
    assert executor._in_flight == {}  # type: ignore[attr-defined]
