"""Minimal in-memory agent registry for Micro-Agent Cloud (C1).

Stores versioned :class:`~cloud.descriptors.AgentDescriptor` entries with
lease-based health: a registration is healthy until its TTL lapses unless
heartbeats renew it. Expired entries stay queryable with their staleness —
per the C0 failure model the registry serves stale descriptors with a stated
age instead of hiding agents that stopped heartbeating.

The registry keeps only control-plane state: semantic descriptors and health
rollups. It is never on an agent's serving path. The HTTP app is a plain
FastAPI surface; deploy it with any ASGI server (``python -m cloud.registry``
runs uvicorn). Authentication for the registry API itself is C2+ work and is
deliberately out of scope here.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException

from cloud.descriptors import AgentDescriptor, DescriptorError

DEFAULT_LEASE_SECONDS = 300.0
# Expired entries remain queryable (stale) for this long before removal.
_STALE_RETENTION_SECONDS = 86_400.0


@dataclass
class RegistryEntry:
    """One registration: the descriptor plus its technical health rollup."""

    descriptor: AgentDescriptor
    registered_at: float
    lease_expires_at: float

    @property
    def healthy(self) -> bool:
        return time.monotonic() < self.lease_expires_at

    def age_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.registered_at)

    def expires_in_seconds(self) -> float:
        return max(0.0, self.lease_expires_at - time.monotonic())


class UnknownAgentError(KeyError):
    """Raised for heartbeat/deregister of an unregistered agent version."""


class InMemoryAgentRegistry:
    """Lease-based descriptor registry, safe for single-process deployment."""

    def __init__(self, *, default_lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        self._default_lease = default_lease_seconds
        self._entries: dict[tuple[str, str], RegistryEntry] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, descriptor: AgentDescriptor, *, ttl_seconds: float | None = None
    ) -> RegistryEntry:
        if not descriptor.name or not descriptor.version:
            raise DescriptorError("descriptor must carry a name and version")
        if descriptor.schema_version != AgentDescriptor().schema_version:
            raise DescriptorError(
                f"unsupported descriptor schema version '{descriptor.schema_version}'"
            )
        ttl = ttl_seconds if ttl_seconds is not None else self._default_lease
        if ttl <= 0:
            raise DescriptorError("registration ttl must be positive")
        now = time.monotonic()
        async with self._lock:
            entry = RegistryEntry(
                descriptor=descriptor,
                registered_at=now,
                lease_expires_at=now + ttl,
            )
            self._entries[(descriptor.name, descriptor.version)] = entry
        return entry

    async def heartbeat(
        self, name: str, version: str, *, ttl_seconds: float | None = None
    ) -> RegistryEntry:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_lease
        async with self._lock:
            entry = self._entries.get((name, version))
            if entry is None:
                raise UnknownAgentError(f"{name}@{version} is not registered")
            entry.lease_expires_at = time.monotonic() + ttl
            return entry

    async def deregister(self, name: str, version: str) -> None:
        async with self._lock:
            if self._entries.pop((name, version), None) is None:
                raise UnknownAgentError(f"{name}@{version} is not registered")

    async def query(
        self,
        *,
        name: str | None = None,
        skill: str | None = None,
        tenant: str | None = None,
        healthy_only: bool = False,
    ) -> list[RegistryEntry]:
        """Return matching entries, newest registration first.

        ``tenant`` filters by declared visibility: entries with empty
        visibility are unrestricted, others match only when the tenant is
        listed. Stale (expired-lease) entries are included unless
        ``healthy_only``; entries past the stale-retention window are
        pruned on read.
        """
        now = time.monotonic()
        async with self._lock:
            self._entries = {
                key: entry
                for key, entry in self._entries.items()
                if now - entry.registered_at < _STALE_RETENTION_SECONDS or entry.healthy
            }
            entries = list(self._entries.values())
        if name is not None:
            entries = [e for e in entries if e.descriptor.name == name]
        if skill is not None:
            entries = [e for e in entries if any(s.id == skill for s in e.descriptor.skills)]
        if tenant is not None:
            entries = [
                e
                for e in entries
                if not e.descriptor.visibility or tenant in e.descriptor.visibility
            ]
        if healthy_only:
            entries = [e for e in entries if e.healthy]
        entries.sort(key=lambda e: (e.descriptor.name, e.descriptor.version))
        return entries

    async def get(self, name: str, version: str) -> RegistryEntry:
        async with self._lock:
            entry = self._entries.get((name, version))
            if entry is None:
                raise UnknownAgentError(f"{name}@{version} is not registered")
            return entry


def _entry_payload(entry: RegistryEntry) -> dict[str, Any]:
    return {
        "descriptor": entry.descriptor.to_dict(),
        "healthy": entry.healthy,
        "registered_at_age_seconds": round(entry.age_seconds(), 3),
        "lease_expires_in_seconds": round(entry.expires_in_seconds(), 3),
    }


def create_registry_app(registry: InMemoryAgentRegistry | None = None) -> FastAPI:
    """FastAPI surface for the registry. The registry API is unauthenticated."""
    app = FastAPI(title="Micro-Agent Cloud Registry", version="0.1.0")
    reg = registry if registry is not None else InMemoryAgentRegistry()
    app.state.registry = reg

    @app.put("/registry/agents/{name}/{version}")
    async def register_agent(name: str, version: str, payload: dict[str, Any]) -> dict[str, Any]:
        body_name = str(payload.get("name", name))
        body_version = str(payload.get("version", version))
        if body_name != name or body_version != version:
            raise HTTPException(
                status_code=422,
                detail="descriptor identity must match the URL path name and version",
            )
        try:
            descriptor = AgentDescriptor.from_dict({**payload, "name": name, "version": version})
            entry = await reg.register(descriptor)
        except DescriptorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _entry_payload(entry)

    @app.post("/registry/agents/{name}/{version}/heartbeat")
    async def heartbeat(name: str, version: str) -> dict[str, Any]:
        try:
            entry = await reg.heartbeat(name, version)
        except UnknownAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _entry_payload(entry)

    @app.delete("/registry/agents/{name}/{version}")
    async def deregister_agent(name: str, version: str) -> dict[str, Any]:
        try:
            await reg.deregister(name, version)
        except UnknownAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "deregistered", "name": name, "version": version}

    @app.get("/registry/agents")
    async def query_agents(
        name: str | None = None,
        skill: str | None = None,
        tenant: str | None = None,
        healthy_only: bool = False,
    ) -> dict[str, Any]:
        entries = await reg.query(name=name, skill=skill, tenant=tenant, healthy_only=healthy_only)
        return {"agents": [_entry_payload(entry) for entry in entries]}

    @app.get("/registry/agents/{name}/{version}")
    async def get_agent(name: str, version: str) -> dict[str, Any]:
        try:
            entry = await reg.get(name, version)
        except UnknownAgentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _entry_payload(entry)

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:
        return {"ready": True}

    return app


def main() -> None:
    """Run the registry standalone: ``python -m cloud.registry``."""
    import uvicorn

    uvicorn.run(create_registry_app(), host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "InMemoryAgentRegistry",
    "RegistryEntry",
    "UnknownAgentError",
    "create_registry_app",
    "main",
]
