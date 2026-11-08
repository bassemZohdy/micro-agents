"""Built-in runtime registrations (the composition root for ``runtimes.*``).

This is the only place in ``micro_agent`` that imports runtime adapters. Each
factory imports its adapter lazily so selecting one runtime never requires the
optional dependency of another (Google ADK, LangGraph).
"""

from __future__ import annotations

from micro_agent.checkpoint import CheckpointStore, InMemoryCheckpointStore, SessionCheckpointStore
from micro_agent.memory import MemoryPolicy
from micro_agent.runtime import (
    AgentRuntime,
    RuntimeBuildContext,
    RuntimeRegistration,
    default_registry,
)


def _session_checkpoints(
    context: RuntimeBuildContext, *, memory_fallback: bool
) -> CheckpointStore | None:
    if context.session_provider is not None:
        return SessionCheckpointStore(
            context.session_provider, ttl_seconds=context.session_ttl_seconds
        )
    if memory_fallback and context.session_persistence == "memory":
        return InMemoryCheckpointStore()
    return None


def _build_custom(context: RuntimeBuildContext) -> AgentRuntime:
    from runtimes.custom import CustomRuntime, CustomRuntimeConfig

    return CustomRuntime(
        CustomRuntimeConfig(
            model_provider=context.model_provider,
            session_provider=context.session_provider,
            checkpoint_store=_session_checkpoints(context, memory_fallback=False),
            memory_provider=context.memory_provider,
            memory_policy=MemoryPolicy() if context.memory_provider is not None else None,
            operation_registry=context.operation_registry,
            approval_store=context.approval_store,
            knowledge_provider=context.knowledge_provider,
            mcp_manager=context.mcp_manager,
            policy=context.policy,
            audit=context.audit,
            telemetry=context.telemetry,
            tool_registry=context.tool_registry,
        )
    )


def _build_google_adk(context: RuntimeBuildContext) -> AgentRuntime:
    from runtimes.google_adk import GoogleAdkRuntime, GoogleAdkRuntimeConfig

    return GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=context.model_provider,
            model_api_key=context.model_api_key,
            memory_provider=context.memory_provider,
            memory_policy=MemoryPolicy() if context.memory_provider is not None else None,
            session_provider=context.session_provider,
            operation_registry=context.operation_registry,
            checkpoint_store=_session_checkpoints(context, memory_fallback=True),
            knowledge_provider=context.knowledge_provider,
            mcp_manager=context.mcp_manager,
            policy=context.policy,
            audit=context.audit,
            telemetry=context.telemetry,
            tool_registry=context.tool_registry,
        )
    )


def _build_langgraph(context: RuntimeBuildContext) -> AgentRuntime:
    from runtimes.langgraph import LangGraphRuntime, LangGraphRuntimeConfig

    return LangGraphRuntime(
        LangGraphRuntimeConfig(
            model_provider=context.model_provider,
            session_provider=context.session_provider,
            memory_provider=context.memory_provider,
            memory_policy=MemoryPolicy() if context.memory_provider is not None else None,
            operation_registry=context.operation_registry,
            knowledge_provider=context.knowledge_provider,
            mcp_manager=context.mcp_manager,
            policy=context.policy,
            audit=context.audit,
            telemetry=context.telemetry,
            tool_registry=context.tool_registry,
            approval_store=context.approval_store,
        )
    )


BUILTIN_RUNTIMES: tuple[RuntimeRegistration, ...] = (
    RuntimeRegistration(
        name="custom",
        factory=_build_custom,
        aliases=("adk", "reference"),
        description="Project-owned bounded model/tool loop (the reference runtime)",
        approval_store=True,
    ),
    RuntimeRegistration(
        name="google-adk",
        factory=_build_google_adk,
        aliases=("googleadk",),
        description="Google ADK adapter (requires the 'adk' extra)",
        native_model=True,
        approval_note=(
            "Google ADK runtime uses native approval continuations and does not map "
            "MICRO_AGENT_APPROVAL_ENDPOINT; use the custom runtime for durable approvals"
        ),
    ),
    RuntimeRegistration(
        name="langgraph",
        factory=_build_langgraph,
        aliases=("lang-graph",),
        description="LangGraph StateGraph adapter (requires the 'langgraph' extra)",
        approval_store=True,
    ),
)


def register_builtin_runtimes() -> None:
    """Register the built-in runtimes once; safe to call repeatedly."""
    for registration in BUILTIN_RUNTIMES:
        try:
            default_registry.get(registration.name)
        except LookupError:
            default_registry.register(registration)
