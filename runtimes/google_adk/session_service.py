"""Google ADK session service backed by the runtime-neutral session SPI."""

# Google ADK is optional and does not publish a py.typed marker. Keep the
# dynamic SDK boundary consistent with runtimes/google_adk/runtime.py.
# mypy: disable_error_code="import-not-found,import-untyped,misc"

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from micro_agent.security import get_invocation_identity
from micro_agent.session import SessionContext, SessionProvider
from runtimes.google_adk.transcript import (
    events_to_messages,
    messages_to_events,
    transcript_digest,
)


def build_adk_session_service(provider: SessionProvider, *, author: str = "micro_agent") -> Any:
    """Build an ADK ``BaseSessionService`` over a Micro-Agent provider.

    ADK owns the session/event model while the provider owns persistence,
    expiry, tenant isolation, and optimistic versioning.  A session is stored
    under its plain id in the Micro-Agent transcript layout (``role``/``content``
    messages), so any runtime can read what ADK wrote and ADK can read what any
    runtime wrote. The native ADK events travel in the session metadata and are
    reloaded losslessly while the transcript is unchanged; if another runtime
    appended to it, events are rebuilt from the transcript (``author`` is the ADK
    agent name they are attributed to). Sessions stored by earlier versions under
    a prefixed key are migrated when first read.
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

        _LEGACY_PREFIX = "__google_adk_session__:"

        @staticmethod
        def _storage_id(app_name: str, user_id: str, session_id: str) -> str:
            del app_name, user_id  # the portable layout is keyed by the session id alone
            return session_id

        @classmethod
        def _legacy_id(cls, app_name: str, user_id: str, session_id: str) -> str:
            return cls._LEGACY_PREFIX + ":".join(
                quote(value, safe="") for value in (app_name, user_id, session_id)
            )

        async def _fetch(
            self, app_name: str, user_id: str, session_id: str, tenant_id: str | None
        ) -> SessionContext | None:
            """Load a session, migrating a legacy ADK-layout record on first read."""
            storage_id = self._storage_id(app_name, user_id, session_id)
            stored = await provider.get(storage_id, tenant_id=tenant_id)
            if stored is not None:
                return stored
            legacy_id = self._legacy_id(app_name, user_id, session_id)
            legacy = await provider.get(legacy_id, tenant_id=tenant_id)
            if legacy is None:
                return None
            session = Session(
                app_name=app_name,
                user_id=user_id,
                id=session_id,
                state=deepcopy(dict(legacy.metadata.get("adk_state") or {})),
                events=[self._load_event(payload) for payload in legacy.messages],
                last_update_time=float(legacy.metadata.get("adk_last_update_time") or 0.0),
            )
            created = await provider.create(storage_id, tenant_id=tenant_id)
            migrated = self._context_from_session(session, context=created, tenant_id=tenant_id)
            await provider.update(migrated, expected_version=migrated.version)
            await provider.delete(legacy_id, tenant_id=tenant_id)
            return await provider.get(storage_id, tenant_id=tenant_id)

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
                    "adk_events": [cls._dump_event(event) for event in session.events],
                }
            )
            messages = events_to_messages(list(session.events))
            metadata["adk_sync"] = transcript_digest(messages)
            return SessionContext(
                session_id=cls._storage_id(session.app_name, session.user_id, session.id),
                messages=messages,
                metadata=metadata,
                tenant_id=tenant_id,
                version=context.version if context is not None else 0,
            )

        @classmethod
        def _session_from_context(
            cls, context: SessionContext, app_name: str, user_id: str, session_id: str
        ) -> Any:
            metadata = context.metadata
            native = metadata.get("adk_events")
            if isinstance(native, list) and metadata.get("adk_sync") == transcript_digest(
                context.messages
            ):
                events = [cls._load_event(payload) for payload in native]
            else:
                # Written (or extended) by another runtime: derive ADK events.
                events = messages_to_events(context.messages, author)
            return Session(
                app_name=str(metadata.get("adk_app_name") or app_name),
                user_id=str(metadata.get("adk_user_id") or user_id),
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
            if await self._fetch(app_name, user_id, adk_session_id, tenant_id) is not None:
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
            context = await self._fetch(app_name, user_id, session_id, self._tenant_id())
            if context is None:
                return None
            return self._session_from_context(context, app_name, user_id, session_id)

        async def list_sessions(self, *, app_name: str, user_id: str | None = None) -> Any:
            sessions: list[Any] = []
            for metadata in await provider.list_active(tenant_id=self._tenant_id()):
                context = await provider.get(metadata.session_id, tenant_id=metadata.tenant_id)
                if context is None or "adk_app_name" not in context.metadata:
                    continue  # not an ADK-visible session
                session = self._session_from_context(
                    context, app_name, user_id or "", metadata.session_id
                )
                if session.app_name != app_name or (
                    user_id is not None and session.user_id != user_id
                ):
                    continue
                session.events = []
                sessions.append(session)
            sessions.sort(key=lambda item: (item.last_update_time, item.user_id, item.id))
            return ListSessionsResponse(sessions=sessions)

        async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
            tenant_id = self._tenant_id()
            await provider.delete(
                self._storage_id(app_name, user_id, session_id), tenant_id=tenant_id
            )
            await provider.delete(
                self._legacy_id(app_name, user_id, session_id), tenant_id=tenant_id
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
            stored = await self._fetch(session.app_name, session.user_id, session.id, tenant_id)
            if stored is None:
                return event
            current = self._session_from_context(
                stored, session.app_name, session.user_id, session.id
            )
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
