"""Concrete MicroAgent implementation — binds definition + runtime with lifecycle."""

from __future__ import annotations

import asyncio

from micro_agent.core.agent import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
    AgentState,
    MicroAgent,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.runtime import AgentRuntime, RuntimeAgent


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
        self._active_invocations = 0
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
                await self._runtime.start(self._runtime_agent)
            except Exception:
                self._state = AgentState.ERROR
                raise
            self._state = AgentState.READY

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        async with self._lifecycle_lock:
            if self._state != AgentState.READY:
                raise RuntimeError(f"Cannot invoke from state {self._state.value}")
            assert self._runtime_agent is not None
            runtime_agent = self._runtime_agent
            self._active_invocations += 1
            self._idle.clear()
        try:
            return await self._runtime.invoke(runtime_agent, request)
        finally:
            async with self._lifecycle_lock:
                self._active_invocations -= 1
                if self._active_invocations == 0:
                    self._idle.set()

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
                runtime_agent = self._runtime_agent

        if wait_for_existing_stop:
            await self._stop_complete.wait()
            return

        await self._idle.wait()
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

    async def shutdown(self) -> None:
        if self._runtime_agent:
            await self._runtime.shutdown(self._runtime_agent)
            self._runtime_agent = None
