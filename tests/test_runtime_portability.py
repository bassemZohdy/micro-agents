"""Shared runtime contract coverage for the custom and Google ADK paths."""

from __future__ import annotations

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelConfig, FakeModelProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig
from runtimes.google_adk import GoogleAdkRuntime, GoogleAdkRuntimeConfig

pytest.importorskip("google.adk")
pytestmark = pytest.mark.adk


def _portable_definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "portable-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Respond with the configured answer."},
                "dependencies": {"model": {"ref": "portable-model", "provider": "fake"}},
            },
        }
    )


@pytest.mark.asyncio
async def test_same_definition_and_provider_run_through_both_runtimes():
    definition = _portable_definition()
    provider = FakeModelProvider(FakeModelConfig(response="portable response"))
    runtimes = [
        AdkRuntime(AdkRuntimeConfig(model_provider=provider)),
        GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider)),
    ]

    for runtime in runtimes:
        agent = await runtime.create(definition)
        try:
            await runtime.start(agent)
            response = await runtime.invoke(
                agent,
                AgentRequest(input={"message": "hello"}, session_id="portable-session"),
            )
            assert response.status == "success"
            assert response.output["content"] == "portable response"
            assert response.session_id == "portable-session"
        finally:
            await runtime.close()
