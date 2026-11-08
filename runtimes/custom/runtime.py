"""Reference model/tool loop (the ``custom`` runtime, package ``runtimes.custom``).

A bounded agent loop (model call -> tool execution -> ...) built on the
framework-neutral :class:`micro_agent.governance.GovernedRuntime`. It does not
integrate Google ADK, and no runtime-native types leak into definition or core
contracts. It was formerly published as ``runtimes.adk``; that name remains a
compatibility alias.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from micro_agent.core import (
    AgentRequest,
    AgentResponse,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.governance import (
    MAX_SESSION_HISTORY_MESSAGES,
    ApprovalPausedError,
    GovernedRuntime,
    GovernedRuntimeConfig,
    InvocationDeadline,
    call_with_supported_kwargs,
    current_model_scope,
    new_call_id,
)
from micro_agent.knowledge import (
    build_knowledge_query,
    retrieve_knowledge_context,
)
from micro_agent.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.models import (
    ModelConfig,
    ModelProvider,
    structured_output_generation,
)
from micro_agent.runtime import RuntimeAgent
from micro_agent.security import (
    PendingApproval,
    get_invocation_identity,
)
from micro_agent.session import SessionProvider
from micro_agent.tools import Tool


class CustomRuntime(GovernedRuntime):
    """The custom reference loop: governance from the base, orchestration here."""

    async def _invoke_inner(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        trace_id: str,
        deadline: InvocationDeadline,
        *,
        side_effect_state: dict[str, Any] | None = None,
        emit_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentResponse:
        definition: MicroAgentDefinition = agent._internal["definition"]
        model_provider: ModelProvider = agent._internal["model_provider"]
        tools: dict[str, Tool] = agent._internal["tools"]
        skills = agent._internal["skills"]
        session_provider: SessionProvider | None = agent._internal["session_provider"]
        session_ttl: int | None = agent._internal["session_ttl"]
        memory_provider: MemoryProvider | None = agent._internal["memory_provider"]
        memory_scope: str = agent._internal["memory_scope"]

        semantics = definition.spec.runtime
        max_iterations = semantics.max_iterations or self._config.default_max_iterations
        labels = {"agent": definition.metadata.name}

        bound_identity = get_invocation_identity()
        tenant_id = (
            bound_identity.user.tenant_id
            if bound_identity is not None and bound_identity.user is not None
            else None
        )

        checkpoint = await self._load_checkpoint(agent, request, deadline, tenant_id)

        # Approval continuation: restore the paused conversation state before
        # anything else. Unknown, expired, or foreign continuations fail fast.
        resume: PendingApproval | None = None
        approved: bool = False
        if request.continuation_id:
            resume, approved = await self._load_pending_approval(agent, request)

        if checkpoint is not None:
            session_id = checkpoint.session_id
            input_payload = dict(checkpoint.input_payload)
        else:
            session_id = request.session_id or (resume.session_id if resume else None)
            input_payload = resume.input_payload if resume is not None else request.input
        agent_span = self._telemetry.start_span(
            "agent.invoke",
            trace_id=trace_id,
            attributes={
                "agent": definition.metadata.name,
                "session_id": session_id,
                "caller_id": (
                    bound_identity.caller.caller_id
                    if bound_identity is not None and bound_identity.caller is not None
                    else None
                ),
            },
        )

        session = None
        # Keep the tail length so completed invocations can persist the full
        # turn (including assistant tool calls and tool results) without
        # duplicating the prior session history.  The system prompt is never
        # persisted.
        history_tail_length = 0
        if session_provider is not None and session_id:
            session = await deadline.run(
                call_with_supported_kwargs(session_provider.get, session_id, tenant_id=tenant_id)
            )
            if session is None:
                session = await deadline.run(
                    call_with_supported_kwargs(
                        session_provider.create,
                        session_id,
                        ttl_seconds=session_ttl,
                        tenant_id=tenant_id,
                    )
                )

        inner_start = time.monotonic()
        knowledge_context = ""
        knowledge_counts: dict[str, int] = {}
        if (
            resume is None
            and checkpoint is None
            and self._config.knowledge_provider is not None
            and self._knowledge_refs
        ):
            knowledge_context, knowledge_counts = await deadline.run(
                retrieve_knowledge_context(
                    self._config.knowledge_provider,
                    build_knowledge_query(input_payload),
                    self._knowledge_refs,
                )
            )

        if checkpoint is not None:
            messages = [dict(message) for message in checkpoint.messages]
            history_tail_length = checkpoint.history_tail_length
        else:
            system_prompt = self._system_prompt(definition, skills)
            if knowledge_context:
                system_prompt += "\n\n" + knowledge_context
            messages = [{"role": "system", "content": system_prompt}]
            if session is not None:
                history_tail = session.messages[-MAX_SESSION_HISTORY_MESSAGES:]
                history_tail_length = len(history_tail)
                messages.extend(history_tail)
            messages.append({"role": "user", "content": json.dumps(input_payload, default=str)})

        model_ref = definition.spec.dependencies.model
        model_config = ModelConfig(
            ref=model_ref.ref if model_ref else "default",
            provider=model_ref.provider if model_ref else None,
            model_id=model_ref.model_id if model_ref else None,
            endpoint=model_ref.endpoint if model_ref else None,
            credential_ref=model_ref.credential_ref if model_ref else None,
            generation=structured_output_generation(
                model_ref.generation if model_ref else {},
                definition.spec.behavior.output_contract,
                model_provider.capabilities(),
            ),
            timeout_seconds=model_ref.timeout_seconds if model_ref else None,
        )
        tool_schemas = [
            {
                "name": tool.metadata.name,
                "description": tool.metadata.description,
                "input_schema": tool.input_schema.parameters,
            }
            for tool in tools.values()
        ]

        all_tool_results: list[dict[str, Any]] = (
            list(checkpoint.all_tool_results) if checkpoint is not None else []
        )
        usage: dict[str, int] = dict(checkpoint.usage) if checkpoint is not None else {}
        iterations = checkpoint.iterations if checkpoint is not None else 0
        max_iterations_reached = False

        if resume is not None:
            # Replay the paused tool wave and continue from the paused transcript
            # (it already ends with the assistant tool-request turn).
            replay = await self._replay_pending_approval(
                agent,
                tools,
                resume,
                approved,
                trace_id=trace_id,
                parent_span_id=agent_span.span_id,
                labels=labels,
                deadline=deadline,
                side_effect_state=side_effect_state,
            )
            iterations = resume.iterations
            all_tool_results = [*resume.all_tool_results, *replay.tool_results]
            messages = replay.messages

        checkpoint_id = request.checkpoint_id or request.request_id
        await self._save_checkpoint(
            agent,
            request,
            deadline,
            tenant_id,
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            input_payload=input_payload,
            messages=messages,
            all_tool_results=all_tool_results,
            iterations=iterations,
            usage=usage,
            history_tail_length=history_tail_length,
        )

        model_scope = current_model_scope()
        if model_scope is not None:
            model_scope.parent_span_id = agent_span.span_id

        while True:
            iterations += 1
            # Deadline, span, latency/usage telemetry and streaming deltas are
            # applied by the governed provider from the bound model scope.
            response = await model_provider.generate(
                model_config, messages, tools=tool_schemas or None
            )
            usage = {k: usage.get(k, 0) + v for k, v in response.usage.items()}

            assistant_message: dict[str, Any] = {"role": "assistant", "content": response.content}
            if response.tool_requests:
                # The assistant tool_calls payload must stay in the history —
                # providers require it to pair the following tool results.
                assistant_message["tool_calls"] = [
                    {
                        "id": str(request.get("id") or new_call_id()),
                        "type": "function",
                        "function": {
                            "name": str(request.get("name", "")),
                            "arguments": json.dumps(request.get("arguments") or {}, default=str),
                        },
                    }
                    for request in response.tool_requests
                ]
                for call, tool_request in zip(
                    assistant_message["tool_calls"], response.tool_requests, strict=True
                ):
                    tool_request["id"] = call["id"]
            messages.append(assistant_message)

            if not response.tool_requests:
                break
            if iterations >= max_iterations:
                max_iterations_reached = True
                self._telemetry.logger.warning(
                    "max_iterations reached with pending tool requests",
                    request_id=request.request_id,
                    max_iterations=max_iterations,
                )
                break

            if self._approval_needed(agent, tools, response.tool_requests):
                approval = await self._save_pending_approval(
                    agent,
                    request,
                    tool_requests=response.tool_requests,
                    messages=messages,
                    all_tool_results=all_tool_results,
                    iterations=iterations,
                    session_id=session_id,
                    input_payload=input_payload,
                )
                await self._delete_checkpoint(checkpoint_id, deadline, tenant_id)
                raise ApprovalPausedError(approval)

            if not self._tool_wave_is_replay_safe(agent, tools, response.tool_requests):
                await self._delete_checkpoint(checkpoint_id, deadline, tenant_id)

            tool_results = await self._execute_tools(
                tools,
                response.tool_requests,
                trace_id,
                agent_span.span_id,
                labels,
                deadline=deadline,
                side_effects=agent._internal.get("tool_side_effects"),
                side_effect_state=side_effect_state,
            )
            all_tool_results.extend(tool_results)
            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.get("tool_call_id"),
                        "name": result["tool"],
                        "content": json.dumps(result["error"] or result["output"], default=str),
                    }
                )
            await self._save_checkpoint(
                agent,
                request,
                deadline,
                tenant_id,
                checkpoint_id=checkpoint_id,
                session_id=session_id,
                input_payload=input_payload,
                messages=messages,
                all_tool_results=all_tool_results,
                iterations=iterations,
                usage=usage,
                history_tail_length=history_tail_length,
            )

        if session is not None:
            # Persist the complete current turn so the next invocation can
            # replay provider-required assistant tool calls alongside their
            # matching tool results.  Only messages after the prior history
            # tail are new; the system prompt remains runtime-only.
            turn_start = 1 + history_tail_length
            session.messages.extend(dict(message) for message in messages[turn_start:])
            await deadline.run(
                session_provider.update(session, ttl_seconds=session_ttl)  # type: ignore[union-attr]
            )

        if (
            memory_provider is not None
            and (self._config.memory_policy or MemoryPolicy()).auto_store
        ):
            await deadline.run(
                call_with_supported_kwargs(
                    memory_provider.store,
                    MemoryEntry(
                        key=f"invocation:{request.request_id or uuid4()}",
                        value={
                            "input": input_payload,
                            "output": response.content,
                        },
                        scope=memory_scope,
                        tenant_id=tenant_id,
                    ),
                )
            )

        await self._delete_checkpoint(checkpoint_id, deadline, tenant_id)

        agent_span.set_attribute("iterations", iterations)
        agent_span.add_event(
            "agent.response",
            {"finish_reason": response.finish_reason, "tools_called": len(all_tool_results)},
        )
        self._telemetry.finish_span(agent_span)

        return AgentResponse(
            output={"content": response.content, "tool_results": all_tool_results},
            request_id=request.request_id,
            session_id=session_id,
            status="success",
            metadata={
                "usage": usage,
                "latency_ms": round((time.monotonic() - inner_start) * 1000, 2),
                "model_ref": model_config.ref,
                "tools_called": [r["tool"] for r in all_tool_results if not r.get("denied")],
                "iterations": iterations,
                "max_iterations_reached": max_iterations_reached,
                "unresolved_tools": agent._internal["unresolved_tools"],
                "knowledge_entries": sum(knowledge_counts.values()),
                "knowledge_sources": knowledge_counts,
                "resumed_from_checkpoint": request.checkpoint_id,
            },
        )


CustomRuntimeConfig = GovernedRuntimeConfig
