"""Concrete MicroAgent implementation — binds definition + runtime with lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

from micro_agent.core.agent import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
    AgentState,
    InvocationOverloadedError,
    MicroAgent,
)
from micro_agent.definition import MicroAgentDefinition, validate_input, validate_output
from micro_agent.definition.models import ConcurrencyPolicy
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities


class DefaultMicroAgent(MicroAgent):
    """Concrete MicroAgent that binds a definition to a runtime with full lifecycle."""

    def __init__(
        self,
        definition: MicroAgentDefinition,
        runtime: AgentRuntime,
    ) -> None:
        self._definition = definition
        self._runtime = runtime
        self._state = AgentState.CREATED
        self._runtime_agent: RuntimeAgent | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._capacity_available = asyncio.Condition(self._lifecycle_lock)
        self._active_invocations = 0
        self._invocation_tasks: set[asyncio.Task[Any]] = set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._stop_complete = asyncio.Event()

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            agent_id=f"{self._definition.metadata.name}-{self._definition.metadata.version}",
            agent_name=self._definition.metadata.name,
            agent_version=self._definition.metadata.version,
        )

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def capabilities(self) -> AgentCapabilities:
        caps = self._runtime.capabilities()
        return AgentCapabilities(
            streaming=caps.streaming,
            structured_output=caps.structured_output,
            memory=caps.memory,
            mcp=caps.mcp,
            a2a=caps.a2a,
        )

    @property
    def runtime_capabilities(self) -> RuntimeCapabilities:
        """Return the complete capability matrix advertised by the runtime."""
        return self._runtime.capabilities()

    @property
    def definition(self) -> MicroAgentDefinition:
        return self._definition

    async def initialize(self) -> None:
        async with self._lifecycle_lock:
            if self._state != AgentState.CREATED:
                raise RuntimeError(f"Cannot initialize from state {self._state.value}")
            self._runtime_agent = await self._runtime.create(self._definition)
            self._state = AgentState.INITIALIZED

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state != AgentState.INITIALIZED:
                raise RuntimeError(f"Cannot start from state {self._state.value}")
            assert self._runtime_agent is not None
            self._state = AgentState.STARTING
            try:
                required = self._definition.spec.runtime.capabilities
                available = self._runtime.capabilities()
                missing = sorted(
                    capability for capability in required if not available.supports(capability)
                )
                if missing:
                    raise RuntimeError(
                        "Runtime does not support required capabilities: " + ", ".join(missing)
                    )
                await self._runtime.start(self._runtime_agent)
            except Exception:
                self._state = AgentState.ERROR
                raise
            self._state = AgentState.READY

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        async with self._lifecycle_lock:
            max_concurrency = self._definition.spec.runtime.max_concurrency
            policy = self._definition.spec.runtime.concurrency_policy
            while max_concurrency is not None and self._active_invocations >= max_concurrency:
                if policy == ConcurrencyPolicy.REJECT:
                    raise InvocationOverloadedError(max_concurrency)
                await self._capacity_available.wait()
            if self._state != AgentState.READY:
                raise RuntimeError(f"Cannot invoke from state {self._state.value}")
            assert self._runtime_agent is not None
            validate_input(self._definition.spec.behavior.input_contract, request.input)
            runtime_agent = self._runtime_agent
            self._active_invocations += 1
            self._idle.clear()
            current_task = asyncio.current_task()
            if current_task is not None:
                self._invocation_tasks.add(current_task)
        try:
            response = await self._runtime.invoke(runtime_agent, request)
            validate_output(self._definition.spec.behavior.output_contract, response.output)
            return response
        finally:
            async with self._lifecycle_lock:
                self._active_invocations -= 1
                if current_task is not None:
                    self._invocation_tasks.discard(current_task)
                if self._active_invocations == 0:
                    self._idle.set()
                self._capacity_available.notify_all()

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if self._state in (AgentState.STOPPED, AgentState.CREATED):
                return
            if self._state == AgentState.STOPPING:
                wait_for_existing_stop = True
                runtime_agent = None
            else:
                assert self._runtime_agent is not None
                self._state = AgentState.STOPPING
                wait_for_existing_stop = False
                self._stop_complete.clear()
                self._capacity_available.notify_all()
                runtime_agent = self._runtime_agent

        if wait_for_existing_stop:
            await self._stop_complete.wait()
            return

        shutdown_timeout = self._definition.spec.runtime.shutdown_timeout_seconds
        try:
            if shutdown_timeout is None:
                await self._idle.wait()
            else:
                await asyncio.wait_for(self._idle.wait(), timeout=shutdown_timeout)
        except TimeoutError:
            await self._cancel_in_flight()
        assert runtime_agent is not None
        try:
            await self._runtime.stop(runtime_agent)
        except Exception:
            async with self._lifecycle_lock:
                self._state = AgentState.ERROR
                self._stop_complete.set()
            raise
        async with self._lifecycle_lock:
            self._state = AgentState.STOPPED
            self._stop_complete.set()

    async def _cancel_in_flight(self) -> None:
        """Cancel invocations that did not drain before the shutdown deadline."""
        current_task = asyncio.current_task()
        async with self._lifecycle_lock:
            tasks = [
                task
                for task in self._invocation_tasks
                if task is not current_task and not task.done()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._idle.wait()

    async def shutdown(self) -> None:
        if self._runtime_agent:
            await self._runtime.shutdown(self._runtime_agent)
            self._runtime_agent = None
