"""Audit sink tests: redaction, selection, and security-decision events."""

from __future__ import annotations

import io
import json

import pytest

from micro_agent.config import BootstrapError, build_audit_sink
from micro_agent.config.config import ResolvedConfig
from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.observability import (
    FileAuditSink,
    JsonlAuditSink,
    NullAuditSink,
)
from micro_agent.security import AgentPolicy
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_jsonl_sink_writes_redacted_events():
    stream = io.StringIO()
    sink = JsonlAuditSink(stream)
    sink.record("policy.tool_denied", tool="echo", reason="denied", api_key="secret-value")
    events = _lines(stream)
    assert len(events) == 1
    assert events[0]["event"] == "policy.tool_denied"
    assert events[0]["tool"] == "echo"
    assert events[0]["api_key"] == "[REDACTED]"


def test_file_sink_appends_and_closes(tmp_path):
    path = tmp_path / "audit.jsonl"
    sink = FileAuditSink(str(path))
    sink.record("auth.failure", route="/v1/invoke", reason="rejected")
    sink.close()
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "auth.failure"


def test_null_sink_drops_events():
    NullAuditSink().record("policy.tool_denied", tool="echo")


def test_build_audit_sink_selection():
    assert isinstance(build_audit_sink(ResolvedConfig()), JsonlAuditSink)
    assert isinstance(build_audit_sink(ResolvedConfig(audit_sink="none")), NullAuditSink)
    assert isinstance(
        build_audit_sink(ResolvedConfig(audit_sink="file", audit_file="a.jsonl")),
        FileAuditSink,
    )


def test_file_sink_requires_path():
    with pytest.raises(BootstrapError, match="AUDIT_FILE"):
        build_audit_sink(ResolvedConfig(audit_sink="file"))


def test_unknown_audit_sink_fails():
    with pytest.raises(BootstrapError, match="Unsupported audit sink"):
        build_audit_sink(ResolvedConfig(audit_sink="syslog"))


@pytest.mark.asyncio
async def test_tool_denial_is_audited():
    stream = io.StringIO()
    from micro_agent.models import FakeModelConfig, FakeModelProvider, ModelResponse

    calls: list[int] = []

    class OneToolProvider(FakeModelProvider):
        async def generate(self, config, messages, tools=None):
            calls.append(1)
            if len(calls) == 1:
                return ModelResponse(
                    tool_requests=[{"name": "echo", "arguments": {"message": "x"}}]
                )
            return ModelResponse(content="done")

    runtime = AdkRuntime(
        AdkRuntimeConfig(
            model_provider=OneToolProvider(FakeModelConfig()),
            policy=AgentPolicy(denied_tools=["echo"]),
            audit=JsonlAuditSink(stream),
        )
    )
    definition = _definition_with_echo()
    agent = DefaultMicroAgent(definition, runtime)
    try:
        await agent.initialize()
        await agent.start()
        await agent.invoke(AgentRequest(input={}))
    finally:
        await agent.stop()
        await agent.shutdown()
        await runtime.close()

    events = _lines(stream)
    denial = next(e for e in events if e["event"] == "policy.tool_denied")
    assert denial["tool"] == "echo"
    assert "denied by policy" in denial["reason"]


def _definition_with_echo():
    from micro_agent.definition import load_definition_from_dict

    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "audit-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "tools": [{"name": "echo", "source": "native"}],
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_approval_flow_is_audited():
    from micro_agent.models import FakeModelConfig, FakeModelProvider
    from micro_agent.security import InMemoryApprovalStore

    stream = io.StringIO()

    class OneToolProvider(FakeModelProvider):
        async def generate(self, config, messages, tools=None):
            if len(self.invocations) == 1:
                self._config.tool_requests = []
            return await super().generate(config, messages, tools=tools)

    runtime = AdkRuntime(
        AdkRuntimeConfig(
            model_provider=OneToolProvider(
                FakeModelConfig(
                    response="done",
                    tool_requests=[{"name": "echo", "arguments": {"message": "x"}}],
                )
            ),
            policy=AgentPolicy(approval_required=True),
            approval_store=InMemoryApprovalStore(),
            audit=JsonlAuditSink(stream),
        )
    )
    definition = _definition_with_echo()
    agent = DefaultMicroAgent(definition, runtime)
    try:
        await agent.initialize()
        await agent.start()
        paused = await agent.invoke(AgentRequest(input={}))
        await agent.invoke(
            AgentRequest(
                input={},
                continuation_id=paused.metadata["continuation_id"],
                approval_decision="approve",
            )
        )
    finally:
        await agent.stop()
        await agent.shutdown()
        await runtime.close()

    events = _lines(stream)
    names = [e["event"] for e in events]
    assert "approval.requested" in names
    assert "approval.granted" in names
    requested = next(e for e in events if e["event"] == "approval.requested")
    assert requested["tools"] == ["echo"]
    assert requested["continuation_id"]
