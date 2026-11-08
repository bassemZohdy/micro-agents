"""Startup health-check matrix for the custom runtime.

Each configured dependency must be probed before the agent is marked ready,
and every failure mode (raising, returning falsey) must surface as a stable
``RuntimeError`` rather than being discovered on the first invocation.
"""

from typing import Any

import pytest

from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelProvider, ProviderCapabilities
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


def _definition(**dependencies: Any):
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "startup-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Startup test agent."},
                "dependencies": {"model": {"ref": "fake-model"}, **dependencies},
            },
        }
    )


class _BoomError(Exception):
    pass


class _Provider(FakeModelProvider):
    def __init__(self, healthy: Any = True) -> None:
        super().__init__()
        self._healthy = healthy

    async def health_check(self) -> bool:
        if isinstance(self._healthy, Exception):
            raise self._healthy
        return bool(self._healthy)


class _Session:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    async def list_active(self) -> list[Any]:
        if self._fail:
            raise _BoomError("session store down")
        return []


class _Memory:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    async def list_entries(self) -> list[Any]:
        if self._fail:
            raise _BoomError("memory down")
        return []


class _Health:
    """Stand-in for stores whose only startup contract is ``health_check``."""

    def __init__(self, result: Any, sync: bool = False) -> None:
        self._result = result
        self._sync = sync
        if sync:
            self.health_check = self._sync_check  # type: ignore[method-assign]

    def _sync_check(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def health_check(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Knowledge:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def health_check(self, source: Any) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Mcp:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def connect_all(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None

    async def health_probe(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"model_provider": _Provider(False)}, "model provider failed"),
        ({"model_provider": _Provider(_BoomError("x"))}, "model provider failed"),
        ({"session_provider": _Session(fail=True)}, "session provider failed"),
        ({"memory_provider": _Memory(fail=True)}, "memory provider failed"),
        ({"checkpoint_store": _Health(False)}, "checkpoint store failed"),
        ({"checkpoint_store": _Health(_BoomError("x"))}, "checkpoint store failed"),
        ({"operation_registry": _Health(False)}, "operation registry failed"),
        ({"operation_registry": _Health(_BoomError("x"))}, "operation registry failed"),
        ({"operation_registry": _Health(False, sync=True)}, "operation registry failed"),
        ({"approval_store": _Health(False)}, "approval store failed"),
        ({"approval_store": _Health(_BoomError("x"))}, "approval store failed"),
    ],
)
@pytest.mark.asyncio
async def test_failed_dependency_probe_blocks_startup(config, message):
    runtime = AdkRuntime(AdkRuntimeConfig(**config))
    agent = await runtime.create(_definition())
    with pytest.raises(RuntimeError, match=message):
        await runtime.start(agent)
    assert agent._internal["started"] is False


@pytest.mark.asyncio
async def test_healthy_dependencies_allow_startup():
    runtime = AdkRuntime(
        AdkRuntimeConfig(
            model_provider=_Provider(True),
            session_provider=_Session(fail=False),
            memory_provider=_Memory(fail=False),
            checkpoint_store=_Health(True),
            operation_registry=_Health(True, sync=True),
            approval_store=_Health(True),
        )
    )
    agent = await runtime.create(_definition())
    await runtime.start(agent)
    assert agent._internal["started"] is True
    await runtime.stop(agent)
    await runtime.shutdown(agent)


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (False, "knowledge source 'policy-kb' is not available"),
        (_BoomError("x"), "knowledge provider failed"),
    ],
)
@pytest.mark.asyncio
async def test_declared_knowledge_source_must_be_available(result, message):
    runtime = AdkRuntime(AdkRuntimeConfig(knowledge_provider=_Knowledge(result)))
    agent = await runtime.create(_definition(knowledge=[{"ref": "policy-kb"}]))
    with pytest.raises(RuntimeError, match=message):
        await runtime.start(agent)


@pytest.mark.asyncio
async def test_declared_tools_require_a_tool_capable_provider():
    class NoTools(_Provider):
        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(tool_use=False)

    runtime = AdkRuntime(AdkRuntimeConfig(model_provider=NoTools()))
    agent = await runtime.create(
        _definition(tools=[{"name": "echo", "description": "Echo", "source": "native"}])
    )
    with pytest.raises(RuntimeError, match="does not support tool use"):
        await runtime.start(agent)
