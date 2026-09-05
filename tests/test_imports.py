"""Verify that all micro_agent packages import successfully."""

import micro_agent
import micro_agent.config
import micro_agent.core
import micro_agent.definition
import micro_agent.interoperability
import micro_agent.interoperability.a2a_server
import micro_agent.lifecycle
import micro_agent.mcp
import micro_agent.memory
import micro_agent.models
import micro_agent.observability
import micro_agent.runtime
import micro_agent.security
import micro_agent.security.approvals
import micro_agent.security.auth
import micro_agent.security.context
import micro_agent.security.credentials
import micro_agent.security.identity
import micro_agent.security.policy
import micro_agent.security.propagation
import micro_agent.security.side_effects
import micro_agent.session
import micro_agent.session.session
import micro_agent.session.sqlite
import micro_agent.skills
import micro_agent.state
import micro_agent.tools
import micro_agent.tools.plugin


def test_all_packages_importable():
    assert micro_agent is not None
