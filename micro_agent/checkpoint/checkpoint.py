"""Runtime-neutral checkpoint persistence and resume contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from micro_agent.session import SessionProvider


@dataclass
class CheckpointRecord:
    """Replay-safe runtime state captured immediately before a model call."""

    checkpoint_id: str
    agent_id: str
    request_id: str
    session_id: str | None
    input_payload: dict[str, Any]
    messages: list[dict[str, Any]]
    all_tool_results: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    history_tail_length: int = 0
    tenant_id: str | None = None


class CheckpointStore(ABC):
    """Persistence contract for resumable runtime checkpoints."""

    @abstractmethod
    async def save(self, checkpoint: CheckpointRecord) -> None:
        """Persist or replace a checkpoint."""

    @abstractmethod
    async def get(
        self, checkpoint_id: str, *, tenant_id: str | None = None
    ) -> CheckpointRecord | None:
        """Load one tenant-scoped checkpoint."""

    @abstractmethod
    async def delete(self, checkpoint_id: str, *, tenant_id: str | None = None) -> None:
        """Delete a checkpoint after completion or before unsafe replay."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return whether the backing store is reachable."""


class InMemoryCheckpointStore(CheckpointStore):
    """Process-local checkpoint store for tests and development."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, CheckpointRecord] = {}

    @staticmethod
    def _key(checkpoint_id: str, tenant_id: str | None) -> str:
        return checkpoint_id if tenant_id is None else f"{tenant_id}\x1f{checkpoint_id}"

    async def save(self, checkpoint: CheckpointRecord) -> None:
        self._checkpoints[self._key(checkpoint.checkpoint_id, checkpoint.tenant_id)] = deepcopy(
            checkpoint
        )

    async def get(
        self, checkpoint_id: str, *, tenant_id: str | None = None
    ) -> CheckpointRecord | None:
        checkpoint = self._checkpoints.get(self._key(checkpoint_id, tenant_id))
        return deepcopy(checkpoint) if checkpoint is not None else None

    async def delete(self, checkpoint_id: str, *, tenant_id: str | None = None) -> None:
        self._checkpoints.pop(self._key(checkpoint_id, tenant_id), None)

    async def health_check(self) -> bool:
        return True


class SessionCheckpointStore(CheckpointStore):
    """Checkpoint adapter backed by an existing SessionProvider.

    Checkpoint durability follows the configured session provider: in-memory is
    process-local, SQLite persists across local process restarts, and Redis can
    be shared by replicas. Reserved checkpoint session ids are an internal
    implementation detail and are never used as conversational session ids.
    """

    _PREFIX = "__micro_agent_checkpoint__:"
    _METADATA_KEY = "micro_agent_checkpoint"

    def __init__(self, provider: SessionProvider, ttl_seconds: int | None = None) -> None:
        self._provider = provider
        self._ttl_seconds = ttl_seconds

    @classmethod
    def _storage_id(cls, checkpoint_id: str) -> str:
        return f"{cls._PREFIX}{checkpoint_id}"

    async def save(self, checkpoint: CheckpointRecord) -> None:
        storage_id = self._storage_id(checkpoint.checkpoint_id)
        context = await self._provider.get(storage_id, tenant_id=checkpoint.tenant_id)
        if context is None:
            context = await self._provider.create(
                storage_id,
                ttl_seconds=self._ttl_seconds,
                tenant_id=checkpoint.tenant_id,
            )
        context.messages = deepcopy(checkpoint.messages)
        context.metadata[self._METADATA_KEY] = {
            "checkpoint_id": checkpoint.checkpoint_id,
            "agent_id": checkpoint.agent_id,
            "request_id": checkpoint.request_id,
            "session_id": checkpoint.session_id,
            "input_payload": deepcopy(checkpoint.input_payload),
            "all_tool_results": deepcopy(checkpoint.all_tool_results),
            "iterations": checkpoint.iterations,
            "usage": dict(checkpoint.usage),
            "history_tail_length": checkpoint.history_tail_length,
        }
        await self._provider.update(context, ttl_seconds=self._ttl_seconds)

    async def get(
        self, checkpoint_id: str, *, tenant_id: str | None = None
    ) -> CheckpointRecord | None:
        context = await self._provider.get(self._storage_id(checkpoint_id), tenant_id=tenant_id)
        if context is None:
            return None
        payload = context.metadata.get(self._METADATA_KEY)
        if not isinstance(payload, dict):
            return None
        return CheckpointRecord(
            checkpoint_id=str(payload.get("checkpoint_id") or checkpoint_id),
            agent_id=str(payload.get("agent_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            session_id=payload.get("session_id"),
            input_payload=deepcopy(payload.get("input_payload") or {}),
            messages=deepcopy(context.messages),
            all_tool_results=deepcopy(payload.get("all_tool_results") or []),
            iterations=int(payload.get("iterations") or 0),
            usage={str(k): int(v) for k, v in (payload.get("usage") or {}).items()},
            history_tail_length=int(payload.get("history_tail_length") or 0),
            tenant_id=context.tenant_id,
        )

    async def delete(self, checkpoint_id: str, *, tenant_id: str | None = None) -> None:
        await self._provider.delete(self._storage_id(checkpoint_id), tenant_id=tenant_id)

    async def health_check(self) -> bool:
        await self._provider.list_active()
        return True
