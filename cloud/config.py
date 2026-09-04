"""Versioned distributed configuration for Micro-Agent Cloud (C2).

Stores definitions and environment overlays as immutable, monotonic versions
per agent. Agents pin a version at start and keep it across config-plane
outages (the C0 stance: the config plane rolls new versions, it never
mutates a running agent). Definitions are validated with the core's own
loader and overlays with the core's :class:`EnvironmentOverlay`, so the
config plane can never store something an agent would fail to boot.

Secrets stay references: definitions carry ``credential_ref`` fields and the
config plane stores exactly what was validated — never resolved values. The
:class:`SecretResolver` protocol (with an environment-variable
implementation) resolves references at use time, integrating existing
secret-management systems without the store ever holding secret material.

The in-memory store is the minimal C2 form; a durable backend replaces, not
extends, it later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from micro_agent.config import EnvironmentOverlay
from micro_agent.definition import load_definition_from_dict


class ConfigValidationError(ValueError):
    """Raised when a definition or overlay fails the core's validation."""


@dataclass
class ConfigRecord:
    """One immutable configuration version."""

    agent: str
    kind: str  # "definition" or "overlay"
    version: int
    payload: dict[str, Any]
    digest: str
    created_at: float


class SecretResolver(Protocol):
    """Resolves credential references to secret values at use time."""

    def resolve(self, reference: str) -> str | None:
        """Return the secret for ``reference`` or ``None`` when absent."""


class EnvironmentSecretResolver:
    """Reads secret references from environment variables.

    The reference is the variable name; values are looked up only when a
    deployment actually needs them, keeping the config plane free of secret
    material. Other resolvers (Vault, cloud secret managers) implement the
    same one-method protocol.
    """

    def resolve(self, reference: str) -> str | None:
        return os.environ.get(reference)


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemoryConfigStore:
    """Append-only versioned definitions and overlays, per agent."""

    def __init__(self) -> None:
        self._history: dict[tuple[str, str], list[ConfigRecord]] = {}
        self._lock = asyncio.Lock()

    async def store_definition(self, agent: str, payload: dict[str, Any]) -> ConfigRecord:
        try:
            load_definition_from_dict(payload)
        except Exception as exc:  # noqa: BLE001 — surface any validation failure as 422
            raise ConfigValidationError(f"invalid definition: {exc}") from exc
        return await self._store(agent, "definition", payload)

    async def store_overlay(self, agent: str, payload: dict[str, Any]) -> ConfigRecord:
        try:
            EnvironmentOverlay.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — surface any validation failure as 422
            raise ConfigValidationError(f"invalid overlay: {exc}") from exc
        return await self._store(agent, "overlay", payload)

    async def _store(self, agent: str, kind: str, payload: dict[str, Any]) -> ConfigRecord:
        if not agent:
            raise ConfigValidationError("agent name must not be empty")
        async with self._lock:
            versions = self._history.setdefault((agent, kind), [])
            record = ConfigRecord(
                agent=agent,
                kind=kind,
                version=len(versions) + 1,
                payload=payload,
                digest=_digest(payload),
                created_at=time.time(),
            )
            versions.append(record)
            return record

    async def get(self, agent: str, kind: str, version: int | None = None) -> ConfigRecord:
        """Return a pinned version, or the latest when ``version`` is None."""
        async with self._lock:
            versions = self._history.get((agent, kind), [])
            if version is None:
                if not versions:
                    raise KeyError(f"{agent} has no stored {kind}")
                return versions[-1]
            for record in versions:
                if record.version == version:
                    return record
            raise KeyError(f"{agent} has no {kind} version {version}")

    async def history(self, agent: str, kind: str) -> list[ConfigRecord]:
        """All versions oldest-first; empty when the agent is unknown."""
        async with self._lock:
            return list(self._history.get((agent, kind), []))

    async def rollback(self, agent: str, kind: str, to_version: int) -> ConfigRecord:
        """Roll back by storing the old content as a brand-new version.

        History is append-only: a rollback never rewrites or removes
        versions, so the previous lineage stays auditable and the new
        version is what freshly starting agents pin.
        """
        target = await self.get(agent, kind, to_version)
        return await self._store(agent, kind, dict(target.payload))


def _record_payload(record: ConfigRecord, include_payload: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent": record.agent,
        "kind": record.kind,
        "version": record.version,
        "digest": record.digest,
        "created_at": record.created_at,
    }
    if include_payload:
        body["payload"] = record.payload
    return body


def create_config_app(store: InMemoryConfigStore | None = None) -> Any:
    """FastAPI surface for the config plane (unauthenticated in C2; C3 work)."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Micro-Agent Cloud Config", version="0.1.0")
    cfg = store if store is not None else InMemoryConfigStore()
    app.state.config_store = cfg

    def _not_found(exc: KeyError) -> HTTPException:
        return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))

    @app.put("/config/agents/{agent}/definition")
    async def put_definition(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            record = await cfg.store_definition(agent, payload)
        except ConfigValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _record_payload(record, include_payload=False)

    @app.put("/config/agents/{agent}/overlay")
    async def put_overlay(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            record = await cfg.store_overlay(agent, payload)
        except ConfigValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _record_payload(record, include_payload=False)

    @app.get("/config/agents/{agent}/definition")
    async def get_definition(agent: str, version: int | None = None) -> dict[str, Any]:
        try:
            record = await cfg.get(agent, "definition", version)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _record_payload(record, include_payload=True)

    @app.get("/config/agents/{agent}/overlay")
    async def get_overlay(agent: str, version: int | None = None) -> dict[str, Any]:
        try:
            record = await cfg.get(agent, "overlay", version)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _record_payload(record, include_payload=True)

    @app.get("/config/agents/{agent}/history")
    async def get_history(agent: str, kind: str = "definition") -> dict[str, Any]:
        if kind not in {"definition", "overlay"}:
            raise HTTPException(status_code=422, detail="kind must be definition or overlay")
        records = await cfg.history(agent, kind)
        return {"versions": [_record_payload(r, include_payload=False) for r in records]}

    @app.post("/config/agents/{agent}/rollback")
    async def post_rollback(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", "definition"))
        to_version = payload.get("to_version")
        if kind not in {"definition", "overlay"}:
            raise HTTPException(status_code=422, detail="kind must be definition or overlay")
        if not isinstance(to_version, int):
            raise HTTPException(status_code=422, detail="to_version must be an integer")
        try:
            record = await cfg.rollback(agent, kind, to_version)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _record_payload(record, include_payload=False)

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:
        return {"ready": True}

    return app


__all__ = [
    "ConfigRecord",
    "ConfigValidationError",
    "EnvironmentSecretResolver",
    "InMemoryConfigStore",
    "SecretResolver",
    "create_config_app",
]
