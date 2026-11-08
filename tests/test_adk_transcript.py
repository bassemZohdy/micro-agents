"""ADK <-> Micro-Agent transcript mapping and legacy session migration."""

from __future__ import annotations

import pytest

from micro_agent.session import InMemorySessionProvider
from runtimes.google_adk.transcript import (
    CONFIRMATION_FUNCTION,
    events_to_messages,
    messages_to_events,
    transcript_digest,
)

pytest.importorskip("google.adk")

pytestmark = pytest.mark.adk

APP, USER, SID = "app", "user", "sess"


def _events():
    from google.adk.events.event import Event
    from google.genai import types

    def part(**kwargs):
        return types.Part(**kwargs)

    text = types.Part.from_text
    return [
        Event(author="user", content=types.Content(role="user", parts=[text(text="hi")])),
        Event(
            author="agent",
            content=types.Content(
                role="model",
                parts=[
                    text(text="checking"),
                    part(function_call=types.FunctionCall(id="c1", name="lookup", args={"q": 1})),
                ],
            ),
        ),
        # ADK protocol plumbing must not reach the portable transcript.
        Event(
            author="agent",
            content=types.Content(
                role="model",
                parts=[
                    part(
                        function_call=types.FunctionCall(
                            id="c2", name=CONFIRMATION_FUNCTION, args={}
                        )
                    )
                ],
            ),
        ),
        Event(
            author="agent",
            content=types.Content(
                role="user",
                parts=[
                    part(
                        function_response=types.FunctionResponse(
                            id="c1", name="lookup", response={"ok": True}
                        )
                    )
                ],
            ),
        ),
        Event(author="agent", content=types.Content(role="model", parts=[text(text="done")])),
        Event(author="agent", partial=True, content=types.Content(role="model", parts=[])),
    ]


def test_events_map_to_the_transcript_layout():
    messages = events_to_messages(_events())
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["tool_calls"][0]["function"] == {"name": "lookup", "arguments": '{"q": 1}'}
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "lookup",
        "content": '{"ok": true}',
    }


def test_the_transcript_rebuilds_equivalent_events():
    messages = events_to_messages(_events())
    rebuilt = messages_to_events(messages, "agent")
    assert events_to_messages(rebuilt) == messages
    assert [e.author for e in rebuilt] == ["user", "agent", "agent", "agent"]


def test_non_object_tool_output_and_unknown_roles_are_handled():
    rebuilt = messages_to_events(
        [
            {"role": "system", "content": "ignored"},
            {"role": "assistant", "content": ""},
            {"role": "tool", "tool_call_id": "x", "name": "t", "content": "plain text"},
        ],
        "agent",
    )
    assert len(rebuilt) == 1
    response = rebuilt[0].content.parts[0].function_response.response
    assert response == {"result": "plain text"}


def test_the_digest_changes_when_the_transcript_changes():
    one = [{"role": "user", "content": "a"}]
    assert transcript_digest(one) == transcript_digest([dict(m) for m in one])
    assert transcript_digest(one) != transcript_digest([*one, {"role": "user", "content": "b"}])


@pytest.mark.asyncio
async def test_native_events_are_reloaded_losslessly_until_another_runtime_appends():
    from runtimes.google_adk.session_service import build_adk_session_service

    sessions = InMemorySessionProvider()
    service = build_adk_session_service(sessions, author="agent")
    session = await service.create_session(app_name=APP, user_id=USER, session_id=SID)
    for event in _events()[:2]:
        await service.append_event(session, event)

    stored = await sessions.get(SID)
    assert stored is not None and [m["role"] for m in stored.messages] == ["user", "assistant"]
    loaded = await service.get_session(app_name=APP, user_id=USER, session_id=SID)
    assert [e.id for e in loaded.events] == [e.id for e in session.events]

    # Another runtime appends a plain message: ADK must see it as an event.
    stored.messages.append({"role": "assistant", "content": "from elsewhere"})
    await sessions.update(stored)
    reloaded = await service.get_session(app_name=APP, user_id=USER, session_id=SID)
    assert reloaded.events[-1].content.parts[0].text == "from elsewhere"
    assert reloaded.events[-1].author == "agent"


@pytest.mark.asyncio
async def test_sessions_stored_in_the_previous_adk_layout_are_migrated_on_read():
    from runtimes.google_adk.session_service import build_adk_session_service

    sessions = InMemorySessionProvider()
    service = build_adk_session_service(sessions, author="agent")
    legacy_id = "__google_adk_session__:" + ":".join((APP, USER, SID))
    legacy = await sessions.create(legacy_id)
    legacy.messages = [e.model_dump(mode="json") for e in _events()[:2]]
    legacy.metadata = {"adk_state": {"k": "v"}, "adk_last_update_time": 1.0}
    await sessions.update(legacy)

    session = await service.get_session(app_name=APP, user_id=USER, session_id=SID)
    assert session is not None and len(session.events) == 2 and session.state == {"k": "v"}
    assert await sessions.get(legacy_id) is None  # moved, not copied
    migrated = await sessions.get(SID)
    assert migrated is not None and [m["role"] for m in migrated.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_listing_skips_sessions_this_service_did_not_write_and_deleting_cleans_up():
    from google.adk.errors.already_exists_error import AlreadyExistsError

    from runtimes.google_adk.session_service import build_adk_session_service

    sessions = InMemorySessionProvider()
    service = build_adk_session_service(sessions, author="agent")
    foreign = await sessions.create("custom-session")
    foreign.messages = [{"role": "user", "content": "hello"}]
    await sessions.update(foreign)
    await service.create_session(app_name=APP, user_id=USER, session_id=SID)

    listed = await service.list_sessions(app_name=APP, user_id=USER)
    assert [s.id for s in listed.sessions] == [SID]
    with pytest.raises(AlreadyExistsError):
        await service.create_session(app_name=APP, user_id=USER, session_id=SID)

    await service.delete_session(app_name=APP, user_id=USER, session_id=SID)
    assert await service.get_session(app_name=APP, user_id=USER, session_id=SID) is None
    # A session another runtime wrote is readable by ADK as events.
    adopted = await service.get_session(app_name=APP, user_id=USER, session_id="custom-session")
    assert adopted is not None and adopted.events[0].content.parts[0].text == "hello"
