"""Config-plane client for Micro-Agent Cloud (C2).

Fetches a pinned or latest definition/overlay version, keeping the last
good payload as a fallback when the config plane is unreachable — running
agents keep their pinned version (the C0 stance), and restarting ones may
start from the last observed configuration instead of failing outright.
"""

from __future__ import annotations

from typing import Any

import httpx


class ConfigPlaneUnreachableError(RuntimeError):
    """Raised when the config plane is down and nothing is cached."""


class ConfigClient:
    """Reads versioned configuration with last-good fallback."""

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
        self._cache: dict[tuple[str, str, int | None], dict[str, Any]] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def put_definition(self, agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.put(
            f"{self._base_url}/config/agents/{agent}/definition", json=payload
        )
        if response.status_code == 422:
            from cloud.config import ConfigValidationError

            raise ConfigValidationError(str(response.json().get("detail", "invalid definition")))
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def put_overlay(self, agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.put(
            f"{self._base_url}/config/agents/{agent}/overlay", json=payload
        )
        if response.status_code == 422:
            from cloud.config import ConfigValidationError

            raise ConfigValidationError(str(response.json().get("detail", "invalid overlay")))
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def get(
        self,
        agent: str,
        kind: str,
        *,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Fetch configuration, falling back to the last good copy.

        The fallback payload is annotated with ``from_cache: true`` and the
        ``version`` that was actually served, so callers can state which
        vintage they are running (C0: pinned versions survive outages).
        """
        if kind not in {"definition", "overlay"}:
            raise ValueError("kind must be definition or overlay")
        key = (agent, kind, version)
        try:
            response = await self._client.get(
                f"{self._base_url}/config/agents/{agent}/{kind}",
                params={"version": version} if version is not None else {},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            cached = self._cache.get(key)
            if cached is None:
                raise ConfigPlaneUnreachableError(
                    f"config plane at {self._base_url} is unavailable and no cached "
                    f"{kind} exists for '{agent}'"
                ) from exc
            return {**cached, "from_cache": True}
        except httpx.HTTPError as exc:
            cached = self._cache.get(key)
            if cached is None:
                raise ConfigPlaneUnreachableError(
                    f"config plane at {self._base_url} is unreachable and no cached "
                    f"{kind} exists for '{agent}'"
                ) from exc
            return {**cached, "from_cache": True}
        body: dict[str, Any] = response.json()
        self._cache[key] = body
        return body


__all__ = ["ConfigClient", "ConfigPlaneUnreachableError"]
