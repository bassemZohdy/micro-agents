"""The runtime registry is the abstraction layer between bootstrap and runtimes."""

from __future__ import annotations

import importlib.metadata
from types import SimpleNamespace

import pytest

from micro_agent.config.bootstrap import BootstrapError, build_runtime
from micro_agent.config.runtimes import BUILTIN_RUNTIMES, register_builtin_runtimes
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelProvider
from micro_agent.runtime import (
    AgentRuntime,
    RuntimeBuildContext,
    RuntimeCapabilities,
    RuntimeRegistration,
    RuntimeRegistry,
    UnknownRuntimeError,
    default_registry,
    get_runtime_registration,
    register_runtime,
    registered_runtimes,
)
from micro_agent.security import InMemoryApprovalStore


class _StubRuntime(AgentRuntime):
    def __init__(self, context: RuntimeBuildContext | None = None) -> None:
        self.context = context

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities()

    async def create(self, definition):  # pragma: no cover - not exercised
        raise NotImplementedError

    async def start(self, agent) -> None:  # pragma: no cover
        raise NotImplementedError

    async def invoke(self, agent, request):  # pragma: no cover
        raise NotImplementedError

    async def stop(self, agent) -> None:  # pragma: no cover
        raise NotImplementedError

    async def shutdown(self, agent) -> None:  # pragma: no cover
        raise NotImplementedError


def _registration(name: str = "stub", **kwargs) -> RuntimeRegistration:
    return RuntimeRegistration(name=name, factory=lambda context: _StubRuntime(context), **kwargs)


def _definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "registry-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "x"},
                "dependencies": {"model": {"ref": "m", "provider": "fake"}},
            },
        }
    )


@pytest.fixture
def registry() -> RuntimeRegistry:
    return RuntimeRegistry(entry_point_group=None)


# ---------------------------------------------------------------------------
# Registry mechanics
# ---------------------------------------------------------------------------


def test_register_and_resolve_by_name_and_alias_is_case_and_separator_insensitive(registry):
    registry.register(_registration("My-Runtime", aliases=("mine", "my_rt")))
    for name in ("my-runtime", "MY_RUNTIME", " mine ", "my-rt"):
        assert registry.get(name).name == "My-Runtime"
    assert registry.names() == ["my-runtime"]


def test_unknown_runtime_raises(registry):
    with pytest.raises(UnknownRuntimeError):
        registry.get("nope")
    with pytest.raises(UnknownRuntimeError):
        registry.unregister("nope")


def test_duplicate_names_and_alias_collisions_are_rejected(registry):
    registry.register(_registration("one", aliases=("uno",)))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_registration("one"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_registration("two", aliases=("uno",)))
    registry.register(_registration("one", aliases=("eins",)), replace=True)
    assert registry.get("eins").name == "one"


def test_unregister_removes_name_and_aliases(registry):
    registry.register(_registration("one", aliases=("uno",)))
    registry.unregister("uno")
    with pytest.raises(UnknownRuntimeError):
        registry.get("one")
    with pytest.raises(UnknownRuntimeError):
        registry.get("uno")


def test_registration_validates_its_fields():
    with pytest.raises(ValueError, match="name"):
        RuntimeRegistration(name="  ", factory=lambda context: _StubRuntime())
    with pytest.raises(TypeError, match="callable"):
        RuntimeRegistration(name="x", factory="not callable")  # type: ignore[arg-type]


def _entry_point(name: str, target):
    class _EntryPoint(SimpleNamespace):
        def load(self):
            return target

    return _EntryPoint(name=name)


@pytest.mark.parametrize("as_callable", [False, True])
def test_entry_point_runtimes_load_lazily_on_selection(monkeypatch, as_callable):
    registration = _registration("plugin-rt")
    target = (lambda: registration) if as_callable else registration
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group=None: [_entry_point("plugin-rt", target)],
    )
    registry = RuntimeRegistry()
    assert registry.names() == ["plugin-rt"]  # discoverable without loading
    assert registry.get("Plugin_RT") is registration
    assert registry.get("plugin-rt") is registration  # now cached


def test_entry_point_must_provide_a_registration(monkeypatch):
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group=None: [_entry_point("bad", lambda: "not a registration")],
    )
    with pytest.raises(TypeError, match="RuntimeRegistration"):
        RuntimeRegistry().get("bad")


# ---------------------------------------------------------------------------
# Built-ins and bootstrap integration
# ---------------------------------------------------------------------------


def test_builtin_runtimes_are_registered_idempotently():
    register_builtin_runtimes()
    register_builtin_runtimes()
    assert {"custom", "google-adk", "langgraph"} <= set(registered_runtimes())
    for registration in BUILTIN_RUNTIMES:
        assert get_runtime_registration(registration.name) is registration
    assert get_runtime_registration("adk").name == "custom"
    assert get_runtime_registration("google_adk").name == "google-adk"
    assert get_runtime_registration("lang_graph").name == "langgraph"


def test_bootstrap_builds_a_third_party_runtime_from_the_registry(monkeypatch):
    register_runtime(_registration("stub-rt"), replace=True)
    try:
        monkeypatch.setenv("MICRO_AGENT_RUNTIME", "stub-rt")
        runtime = build_runtime(_definition()).runtime
        assert isinstance(runtime, _StubRuntime)
        context = runtime.context
        assert context is not None
        assert isinstance(context.model_provider, FakeModelProvider)
        assert context.definition.metadata.name == "registry-agent"
        assert context.approval_store is None  # the stub does not consume one
    finally:
        default_registry.unregister("stub-rt")


def test_bootstrap_rejects_unknown_runtimes_listing_the_registered_ones(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "does-not-exist")
    with pytest.raises(BootstrapError, match="Unsupported runtime.*custom.*google-adk.*langgraph"):
        build_runtime(_definition())


def test_a_runtime_that_ignores_approval_stores_must_say_why(monkeypatch):
    register_runtime(_registration("no-approval", approval_note="no approvals here"), replace=True)
    try:
        monkeypatch.setenv("MICRO_AGENT_RUNTIME", "no-approval")
        monkeypatch.setenv("MICRO_AGENT_APPROVAL_ENDPOINT", "redis://approvals.example.test/0")
        with pytest.raises(BootstrapError, match="no approvals here"):
            build_runtime(_definition())
    finally:
        default_registry.unregister("no-approval")


def test_a_runtime_that_consumes_approvals_receives_the_store(monkeypatch):
    register_runtime(_registration("wants-approval", approval_store=True), replace=True)
    try:
        monkeypatch.setenv("MICRO_AGENT_RUNTIME", "wants-approval")
        store = InMemoryApprovalStore()
        runtime = build_runtime(_definition(), approval_store=store).runtime
        assert isinstance(runtime, _StubRuntime)
        assert runtime.context is not None and runtime.context.approval_store is store
    finally:
        default_registry.unregister("wants-approval")
