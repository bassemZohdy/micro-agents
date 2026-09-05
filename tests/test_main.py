"""Unit tests for the executable Micro-Agent entrypoint."""

from __future__ import annotations

import sys
from argparse import Namespace
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

from micro_agent import __main__ as main_module


class FakeRuntime:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def health_probes(self) -> dict[str, object]:
        return {}

    async def close(self) -> None:
        self.calls.append("runtime.close")


class FakeAgent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def initialize(self) -> None:
        self.calls.append("agent.initialize")

    async def start(self) -> None:
        self.calls.append("agent.start")

    async def stop(self) -> None:
        self.calls.append("agent.stop")

    async def shutdown(self) -> None:
        self.calls.append("agent.shutdown")


class FakeServer:
    def __init__(self, calls: list[str], config: object) -> None:
        self.calls = calls
        self.config = config

    async def serve(self) -> None:
        self.calls.append("server.serve")


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["micro-agent", "--definition", "agent.yaml"])

    args = main_module.parse_args()

    assert args.definition == Path("agent.yaml")
    assert args.port == 8080
    assert args.host == "0.0.0.0"


def test_parse_args_accepts_runtime_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["micro-agent", "--definition", "agent.yaml", "--port", "9000", "--host", "127.0.0.1"],
    )

    args = main_module.parse_args()

    assert args.port == 9000
    assert args.host == "127.0.0.1"


@pytest.mark.asyncio
async def test_run_starts_and_closes_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    definition = object()
    telemetry = object()
    runtime = FakeRuntime(calls)
    agent = FakeAgent(calls)
    bootstrap = SimpleNamespace(
        runtime=runtime,
        resolved=SimpleNamespace(log_level="debug", cors_origins=[]),
    )
    config = object()

    monkeypatch.setattr(main_module, "load_definition_from_file", lambda path: definition)
    monkeypatch.setattr(main_module.Telemetry, "from_environment", lambda: telemetry)
    monkeypatch.setattr(main_module, "build_runtime", lambda value, telemetry: bootstrap)
    monkeypatch.setattr(main_module, "DefaultMicroAgent", lambda value, value_runtime: agent)
    monkeypatch.setattr(main_module, "build_authenticator", lambda resolved: "auth")
    monkeypatch.setattr(main_module, "build_audit_sink", lambda resolved: "audit")
    monkeypatch.setattr(main_module, "create_app", lambda *args, **kwargs: "app")
    monkeypatch.setattr(main_module.uvicorn, "Config", lambda *args, **kwargs: config)
    monkeypatch.setattr(main_module.uvicorn, "Server", lambda value: FakeServer(calls, value))

    await main_module.run(Namespace(definition=Path("agent.yaml"), port=9000, host="127.0.0.1"))

    assert calls == [
        "agent.initialize",
        "agent.start",
        "server.serve",
        "agent.stop",
        "agent.shutdown",
        "runtime.close",
    ]


def test_main_dispatches_to_async_run(monkeypatch: pytest.MonkeyPatch) -> None:
    args = Namespace(definition=Path("agent.yaml"), port=8080, host="0.0.0.0")
    seen: list[Namespace] = []

    monkeypatch.setattr(main_module, "parse_args", lambda: args)

    async def fake_run(value: Namespace) -> None:
        seen.append(value)

    monkeypatch.setattr(main_module, "run", fake_run)

    def fake_asyncio_run(coroutine: object) -> None:
        with suppress(StopIteration):
            coroutine.send(None)  # type: ignore[attr-defined]

    monkeypatch.setattr(main_module.asyncio, "run", fake_asyncio_run)

    main_module.main()

    assert seen == [args]
