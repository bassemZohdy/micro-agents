"""Health-aware discovery client for Micro-Agent Cloud (C1).

Wraps the registry's HTTP surface and adds the C0 failure stance: when the
registry cannot be reached, discovery degrades to the last successful
snapshot instead of failing — results are marked stale so callers can state
the staleness bound they are operating under. Discovery never sits on a
serving path; it only resolves addresses and descriptors.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from cloud.descriptors import AgentDescriptor, DescriptorError


@dataclass
class DiscoveredAgent:
    """One registry hit: the descriptor plus its technical health rollup."""

    descriptor: AgentDescriptor
    healthy: bool
    lease_expires_in_seconds: float
    registered_at_age_seconds: float
    # True when the result came from the local cache because the registry
    # was unreachable; the numbers above then describe the snapshot time.
    from_cache: bool = False


@dataclass
class _Snapshot:
    fetched_at_monotonic: float
    agents: list[DiscoveredAgent] = field(default_factory=list)


class RegistryUnreachableError(RuntimeError):
    """Raised when the registry is down and no cached snapshot exists."""


class RegistryDiscoveryClient:
    """Discovers agents through the registry, degrading to a stale cache."""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._cache: dict[tuple[Any, ...], _Snapshot] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def register(
        self, descriptor: AgentDescriptor, *, ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        if not descriptor.name or not descriptor.version:
            raise DescriptorError("descriptor must carry a name and version")
        payload = descriptor.to_dict()
        params = {"ttl_seconds": ttl_seconds} if ttl_seconds is not None else None
        response = await self._client.put(
            f"{self._base_url}/registry/agents/{descriptor.name}/{descriptor.version}",
            params=params,
            json=payload,
        )
        if response.status_code == 422:
            raise DescriptorError(str(response.json().get("detail", "invalid descriptor")))
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def heartbeat(
        self, name: str, version: str, *, ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        params = {"ttl_seconds": ttl_seconds} if ttl_seconds is not None else None
        response = await self._client.post(
            f"{self._base_url}/registry/agents/{name}/{version}/heartbeat",
            params=params,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def deregister(self, name: str, version: str) -> None:
        response = await self._client.delete(f"{self._base_url}/registry/agents/{name}/{version}")
        response.raise_for_status()

    async def discover(
        self,
        *,
        name: str | None = None,
        skill: str | None = None,
        tenant: str | None = None,
        healthy_only: bool = False,
    ) -> list[DiscoveredAgent]:
        """Resolve agents from the registry, or from the last snapshot.

        On a registry outage the most recent cached snapshot for this exact
        query is returned with ``from_cache=True`` on every hit; with no
        snapshot, :class:`RegistryUnreachableError` is raised. Callers get a
        usable answer or an explicit failure, never a hang.
        """
        key = ("q", name, skill, tenant, healthy_only)
        params: dict[str, Any] = {}
        if name is not None:
            params["name"] = name
        if skill is not None:
            params["skill"] = skill
        if tenant is not None:
            params["tenant"] = tenant
        if healthy_only:
            params["healthy_only"] = "true"
        try:
            response = await self._client.get(f"{self._base_url}/registry/agents", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            snapshot = self._cache.get(key)
            if snapshot is None:
                raise RegistryUnreachableError(
                    f"registry at {self._base_url} is unavailable and no cached "
                    "discovery snapshot exists for this query"
                ) from exc
            return [
                DiscoveredAgent(
                    descriptor=agent.descriptor,
                    healthy=agent.healthy,
                    lease_expires_in_seconds=agent.lease_expires_in_seconds,
                    registered_at_age_seconds=agent.registered_at_age_seconds,
                    from_cache=True,
                )
                for agent in snapshot.agents
            ]
        except httpx.HTTPError as exc:
            snapshot = self._cache.get(key)
            if snapshot is None:
                raise RegistryUnreachableError(
                    f"registry at {self._base_url} is unreachable and no cached "
                    "discovery snapshot exists for this query"
                ) from exc
            return [
                DiscoveredAgent(
                    descriptor=agent.descriptor,
                    healthy=agent.healthy,
                    lease_expires_in_seconds=agent.lease_expires_in_seconds,
                    registered_at_age_seconds=agent.registered_at_age_seconds,
                    from_cache=True,
                )
                for agent in snapshot.agents
            ]
        agents = [
            DiscoveredAgent(
                descriptor=AgentDescriptor.from_dict(item["descriptor"]),
                healthy=bool(item["healthy"]),
                lease_expires_in_seconds=float(item["lease_expires_in_seconds"]),
                registered_at_age_seconds=float(item["registered_at_age_seconds"]),
            )
            for item in response.json().get("agents", [])
        ]
        self._cache[key] = _Snapshot(fetched_at_monotonic=time.monotonic(), agents=agents)
        return agents


__all__ = [
    "DiscoveredAgent",
    "RegistryDiscoveryClient",
    "RegistryUnreachableError",
]
