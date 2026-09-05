"""Tests for the public cloud package surface."""

from __future__ import annotations

import cloud


def test_cloud_reexports_all_implemented_control_plane_surfaces() -> None:
    expected = {
        "ConfigClient",
        "ConfigRecord",
        "Gateway",
        "InMemoryAgentRegistry",
        "InMemoryConfigStore",
        "InMemoryObservabilityStore",
        "RegistryDiscoveryClient",
        "StaticTokenAuthenticator",
        "TraceSpan",
        "UsageRecord",
        "create_config_app",
        "create_gateway_app",
        "create_observability_app",
        "create_registry_app",
    }

    assert expected <= set(cloud.__all__)
    for name in expected:
        assert getattr(cloud, name) is not None
