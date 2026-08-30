"""Verify that all micro_agent packages import successfully."""

import micro_agent
import micro_agent.config
import micro_agent.core
import micro_agent.definition
import micro_agent.interoperability
import micro_agent.lifecycle
import micro_agent.mcp
import micro_agent.memory
import micro_agent.models
import micro_agent.observability
import micro_agent.runtime
import micro_agent.session
import micro_agent.skills
import micro_agent.tools


def test_all_packages_importable():
    assert micro_agent is not None
