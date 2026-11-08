"""Session hand-over between runtimes over real Redis and PostgreSQL state.

The ADK session service stores the portable transcript plus native events, which
is the most state any provider has to hold. This proves the handover works over
the production providers, not only the in-memory one.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from micro_agent.core import AgentRequest
from tests.test_runtime_conformance import _definition, _google_adk, _importable
from tests.test_session_portability import _FACTORIES, _first_turn, _Recorder

pytestmark = [pytest.mark.integration, pytest.mark.adk]

pytest.importorskip("redis")
pytest.importorskip("asyncpg")
if not _importable("google.adk"):  # pragma: no cover - environment dependent
    pytest.skip("google.adk is not installed", allow_module_level=True)


def _redis():
    from micro_agent.session import RedisSessionProvider

    endpoint = os.getenv("MICRO_AGENT_REDIS_URL", "redis://localhost:6379/0")
    return RedisSessionProvider(endpoint, namespace=f"portable-{uuid4().hex}", ttl_seconds=120)


def _postgres():
    from micro_agent.session import PostgresSessionProvider

    dsn = os.environ.get("MICRO_AGENT_TEST_PG_DSN", "")
    if not dsn:
        pytest.skip("MICRO_AGENT_TEST_PG_DSN is not set; PostgreSQL tests skipped")
    return PostgresSessionProvider(dsn, ttl_seconds=120)


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", [_redis, _postgres], ids=["redis", "postgres"])
@pytest.mark.parametrize(
    ("writer", "reader"), [("custom", "google-adk"), ("google-adk", "custom")], ids=str
)
async def test_sessions_move_between_runtimes_over_external_state(make_provider, writer, reader):
    sessions = make_provider()
    session_id = f"portable-{uuid4().hex[:8]}"
    try:
        await _first_turn(_FACTORIES[writer][0], sessions, session_id=session_id)
        stored = await sessions.get(session_id)
        assert stored is not None
        assert {m["role"] for m in stored.messages} <= {"user", "assistant", "tool"}

        recorder = _Recorder()
        runtime = _FACTORIES[reader][0](recorder, session_provider=sessions)
        agent = await runtime.create(_definition())
        try:
            await runtime.start(agent)
            await runtime.invoke(
                agent, AgentRequest(input={"message": "second-turn"}, session_id=session_id)
            )
        finally:
            await runtime.shutdown(agent)
            await runtime.close()
        history = json.dumps(recorder.seen[0], default=str)
        assert "remember-me" in history and "first answer" in history
    finally:
        await sessions.delete(session_id)
        close = getattr(sessions, "aclose", None)
        if close is not None:
            await close()


@pytest.mark.asyncio
@pytest.mark.parametrize("make_provider", [_redis, _postgres], ids=["redis", "postgres"])
async def test_adk_resumes_its_own_session_from_another_process_over_external_state(
    make_provider,
):
    """Two ADK runtimes sharing only the provider behave like two replicas."""
    sessions = make_provider()
    session_id = f"replica-{uuid4().hex[:8]}"
    try:
        await _first_turn(_google_adk, sessions, session_id=session_id)
        recorder = _Recorder()
        runtime = _google_adk(recorder, session_provider=sessions)
        agent = await runtime.create(_definition())
        try:
            await runtime.start(agent)
            await runtime.invoke(
                agent, AgentRequest(input={"message": "again"}, session_id=session_id)
            )
        finally:
            await runtime.shutdown(agent)
            await runtime.close()
        assert "remember-me" in json.dumps(recorder.seen[0], default=str)
    finally:
        await sessions.delete(session_id)
        close = getattr(sessions, "aclose", None)
        if close is not None:
            await close()
