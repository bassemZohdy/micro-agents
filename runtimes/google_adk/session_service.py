"""Google ADK session service backed by the runtime-neutral session SPI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from micro_agent.security import get_invocation_identity
from micro_agent.session import SessionContext, SessionProvider


def build_adk_session_service(provider: SessionProvider) -> Any:
    """Build an ADK ``BaseSessionService`` over a Micro-Agent provider.

    ADK owns the session/event model while the provider owns persistence,
    expiry, tenant isolation, and optimistic versioning.  The adapter stores
    the complete ADK event transcript as JSON-compatible session messages so
    SQLite, Redis, and PostgreSQL providers can all restore an identical ADK
    session after a process restart or replica change.
    """

    try:
        from google.adk.errors.already_exists_error import AlreadyExistsError
        from google.adk.events.event import Event
        from google.adk.sessions.base_session_service import (
            BaseSessionService,
            ListSessionsResponse,
        )
        from google.adk.sessions.session import Session
    except ImportError as exc:  # pragma: no cover - guarded by runtime loading
        raise RuntimeError("Google ADK session APIs are unavailable") from exc

    class ProviderSessionService(BaseSessionService):
        """Persist ADK sessions through a Micro-Agent ``SessionProvider``."""

        _PREFIX = "__google_adk_session__:"

        @classmethod
        def _storage_id(cls, app_name: str, user_id: str, session_id: str) -> str:
            return cls._PREFIX + ":".join(
                quote(value, safe="") for value in (app_name, user_id, session_id)
            )

        @staticmethod
        def _tenant_id() -> str | None:
            identity = get_invocation_identity()
            if identity is None or identity.user is None:
                return None
            return identity.user.tenant_id

        @staticmethod
        def _dump_event(event: Any) -> dict[str, Any]:
            dump = getattr(event, "model_dump", None)
            if not callable(dump):
                raise TypeError("ADK event does not support JSON serialization")
            return cast(dict[str, Any], dump(mode="json"))

        @staticmethod
        def _load_event(payload: Any) -> Any:
            if not isinstance(payload, dict):
                raise ValueError("stored ADK event is not an object")
            return Event.model_validate(payload)

        @classmethod
        def _context_from_session(
            cls,
            session: Any,
            *,
            context: SessionContext | None = None,
            tenant_id: str | None,
        ) -> SessionContext:
            metadata = deepcopy(context.metadata) if context is not None else {}
            metadata.update(
                {
                    "adk_app_name": str(session.app_name),
                    "adk_user_id": str(session.user_id),
                    "adk_session_id": str(session.id),
                    "adk_state": deepcopy(dict(session.state or {})),
                    "adk_last_update_time": float(session.last_update_time),
                }
            )
            return SessionContext(
                session_id=cls._storage_id(session.app_name, session.user_id, session.id),
                messages=[cls._dump_event(event) for event in session.events],
                metadata=metadata,
                tenant_id=tenant_id,
                version=context.version if context is not None else 0,
            )

        @classmethod
        def _session_from_context(cls, context: SessionContext) -> Any:
            metadata = context.metadata
            app_name = str(metadata.get("adk_app_name") or "")
            user_id = str(metadata.get("adk_user_id") or "")
            session_id = str(metadata.get("adk_session_id") or "")
            if not app_name or not user_id or not session_id:
                raise ValueError("stored ADK session metadata is incomplete")
            events = [cls._load_event(payload) for payload in context.messages]
            return Session(
                app_name=app_name,
                user_id=user_id,
                id=session_id,
                state=deepcopy(dict(metadata.get("adk_state") or {})),
                events=events,
                last_update_time=float(metadata.get("adk_last_update_time") or 0.0),
            )

        async def create_session(
            self,
            *,
            app_name: str,
            user_id: str,
            state: dict[str, Any] | None = None,
            session_id: str | None = None,
        ) -> Any:
            adk_session_id = (session_id or "").strip() or str(uuid4())
            tenant_id = self._tenant_id()
            storage_id = self._storage_id(app_name, user_id, adk_session_id)
            if await provider.get(storage_id, tenant_id=tenant_id) is not None:
                raise AlreadyExistsError(f"Session {adk_session_id} already exists.")
            session = Session(
                app_name=app_name,
                user_id=user_id,
                id=adk_session_id,
                state=deepcopy(state or {}),
                events=[],
            )
            context = await provider.create(storage_id, tenant_id=tenant_id)
            context = self._context_from_session(
                session,
                context=context,
                tenant_id=tenant_id,
            )
            await provider.update(context, expected_version=context.version)
            return session

        async def get_session(
            self,
            *,
            app_name: str,
            user_id: str,
            session_id: str,
            config: Any = None,
        ) -> Any:
            del config
            context = await provider.get(
                self._storage_id(app_name, user_id, session_id),
                tenant_id=self._tenant_id(),
            )
            if context is None:
                return None
            return self._session_from_context(context)

        async def list_sessions(self, *, app_name: str, user_id: str | None = None) -> Any:
            sessions: list[Any] = []
            for metadata in await provider.list_active(tenant_id=self._tenant_id()):
                context = await provider.get(metadata.session_id, tenant_id=metadata.tenant_id)
                if context is None:
                    continue
                session = self._session_from_context(context)
                if session.app_name != app_name or (
                    user_id is not None and session.user_id != user_id
                ):
                    continue
                session.events = []
                sessions.append(session)
            sessions.sort(key=lambda item: (item.last_update_time, item.user_id, item.id))
            return ListSessionsResponse(sessions=sessions)

        async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
            await provider.delete(
                self._storage_id(app_name, user_id, session_id),
                tenant_id=self._tenant_id(),
            )

        async def get_user_state(self, *, app_name: str, user_id: str) -> dict[str, Any]:
            response = await self.list_sessions(app_name=app_name, user_id=user_id)
            state: dict[str, Any] = {}
            for session in response.sessions:
                for key, value in session.state.items():
                    if key.startswith("user:"):
                        state[key.removeprefix("user:")] = value
            return state

        async def append_event(self, session: Any, event: Any) -> Any:
            if event.partial:
                return event
            tenant_id = self._tenant_id()
            storage_id = self._storage_id(session.app_name, session.user_id, session.id)
            stored = await provider.get(storage_id, tenant_id=tenant_id)
            if stored is None:
                return event
            current = self._session_from_context(stored)
            if any(existing.id == event.id and existing == event for existing in current.events):
                return event
            await super().append_event(current, event)
            current.last_update_time = event.timestamp
            updated = self._context_from_session(
                current,
                context=stored,
                tenant_id=tenant_id,
            )
            await provider.update(updated, expected_version=stored.version)
            session.state = current.state
            session.events = current.events
            session.last_update_time = current.last_update_time
            return event

    return ProviderSessionService()


__all__ = ["build_adk_session_service"]
