"""Verify that all micro_agent packages import successfully."""

import subprocess
import sys

import pytest

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


_TOP_LEVEL_PACKAGES = [
    "micro_agent",
    "micro_agent.checkpoint",
    "micro_agent.config",
    "micro_agent.core",
    "micro_agent.definition",
    "micro_agent.health",
    "micro_agent.interoperability",
    "micro_agent.knowledge",
    "micro_agent.mcp",
    "micro_agent.memory",
    "micro_agent.models",
    "micro_agent.observability",
    "micro_agent.resilience",
    "micro_agent.runtime",
    "micro_agent.security",
    "micro_agent.session",
    "micro_agent.skills",
    "micro_agent.tools",
]


@pytest.mark.parametrize("package", _TOP_LEVEL_PACKAGES)
def test_package_imports_first_in_fresh_interpreter(package: str) -> None:
    """Every package must import cleanly regardless of import order (no cycles)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {package}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
