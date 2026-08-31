"""Micro-Agent Configuration Framework.

Implements configuration precedence:
    Framework Defaults → Definition → Environment → Secret Bindings
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

_UNSET = object()


class SecretRef(BaseModel, extra="forbid"):
    """Reference to an external secret."""

    name: str = Field(..., min_length=1, description="Secret name/key.")
    source: str | None = Field(None, description="Secret source (env, vault, k8s-secret).")


class EnvironmentConfig(BaseModel, extra="forbid"):
    """Environment-specific configuration overrides."""

    runtime: str | None = None
    model_id: str | None = None
    model_provider: str | None = None
    model_endpoint: str | None = None
    model_api_key_ref: SecretRef | None = None
    mcp_endpoints: dict[str, str] = Field(default_factory=dict)
    memory_endpoint: str | None = None
    session_endpoint: str | None = None
    log_level: str | None = None
    auth: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    audit_sink: str | None = None
    audit_file: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ResolvedConfig:
    """Final resolved configuration after applying precedence."""

    runtime: str | None = None
    model_ref: str | None = None
    model_id: str | None = None
    model_provider: str | None = None
    model_endpoint: str | None = None
    model_api_key: str | None = None
    model_generation: dict[str, Any] = field(default_factory=dict)
    model_timeout_seconds: int | None = None
    mcp_endpoints: dict[str, str] = field(default_factory=dict)
    memory_endpoint: str | None = None
    session_endpoint: str | None = None
    log_level: str = "INFO"
    auth: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    audit_sink: str = "stdout"
    audit_file: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Environment variable resolution
# ---------------------------------------------------------------------------

_ENV_PREFIX = "MICRO_AGENT_"


def _env_key(name: str) -> str:
    return f"{_ENV_PREFIX}{name.upper()}"


def _read_env(name: str) -> str | None:
    return os.environ.get(_env_key(name))


def _resolve_secret(ref: SecretRef) -> str | None:
    """Resolve a secret reference to its value.

    Currently supports:
    - env: read from environment variable
    """
    if ref.source == "env" or ref.source is None:
        return os.environ.get(ref.name)
    return None


# ---------------------------------------------------------------------------
# Configuration resolver
# ---------------------------------------------------------------------------


def resolve_config(
    definition_overrides: dict[str, Any] | None = None,
    env_config: EnvironmentConfig | None = None,
) -> ResolvedConfig:
    """Resolve configuration using precedence:
    1. Framework defaults
    2. Definition overrides
    3. Environment configuration
    4. Secret bindings
    """
    config = ResolvedConfig()

    # Layer 1: Framework defaults (already set in dataclass)

    # Layer 2: Definition overrides
    if definition_overrides:
        for key, value in definition_overrides.items():
            if hasattr(config, key) and value is not None:
                setattr(config, key, value)

    # Layer 3: Environment configuration
    if env_config:
        if env_config.runtime:
            config.runtime = env_config.runtime
        if env_config.model_id:
            config.model_id = env_config.model_id
        if env_config.model_provider:
            config.model_provider = env_config.model_provider
        if env_config.model_endpoint:
            config.model_endpoint = env_config.model_endpoint
        if env_config.mcp_endpoints:
            config.mcp_endpoints.update(env_config.mcp_endpoints)
        if env_config.memory_endpoint:
            config.memory_endpoint = env_config.memory_endpoint
        if env_config.session_endpoint:
            config.session_endpoint = env_config.session_endpoint
        if env_config.log_level is not None:
            config.log_level = env_config.log_level
        if env_config.auth:
            config.auth = env_config.auth
        if env_config.auth_issuer:
            config.auth_issuer = env_config.auth_issuer
        if env_config.auth_audience:
            config.auth_audience = env_config.auth_audience
        if env_config.audit_sink:
            config.audit_sink = env_config.audit_sink
        if env_config.audit_file:
            config.audit_file = env_config.audit_file
        config.extra.update(env_config.extra)

    # Layer 3b: Environment variable overrides
    env_runtime = _read_env("runtime")
    if env_runtime:
        config.runtime = env_runtime

    env_model_endpoint = _read_env("model_endpoint")
    if env_model_endpoint:
        config.model_endpoint = env_model_endpoint

    env_model_id = _read_env("model_id")
    if env_model_id:
        config.model_id = env_model_id

    env_model_api_key = _read_env("model_api_key")
    if env_model_api_key:
        config.model_api_key = env_model_api_key

    env_log_level = _read_env("log_level")
    if env_log_level:
        config.log_level = env_log_level

    env_model_provider = _read_env("model_provider")
    if env_model_provider:
        config.model_provider = env_model_provider

    env_memory_endpoint = _read_env("memory_endpoint")
    if env_memory_endpoint:
        config.memory_endpoint = env_memory_endpoint

    env_session_endpoint = _read_env("session_endpoint")
    if env_session_endpoint:
        config.session_endpoint = env_session_endpoint

    env_auth = _read_env("auth")
    if env_auth:
        config.auth = env_auth

    env_auth_issuer = _read_env("auth_issuer")
    if env_auth_issuer:
        config.auth_issuer = env_auth_issuer

    env_auth_audience = _read_env("auth_audience")
    if env_auth_audience:
        config.auth_audience = env_auth_audience

    env_audit_sink = _read_env("audit_sink")
    if env_audit_sink:
        config.audit_sink = env_audit_sink

    env_audit_file = _read_env("audit_file")
    if env_audit_file:
        config.audit_file = env_audit_file

    # Layer 4: Secret bindings
    if env_config and env_config.model_api_key_ref:
        config.model_api_key = _resolve_secret(env_config.model_api_key_ref)

    return config


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ConfigDiagnostic:
    """A configuration diagnostic message."""

    level: str  # "error", "warning", "info"
    message: str
    path: str = ""


def validate_config(config: ResolvedConfig) -> list[ConfigDiagnostic]:
    """Validate a resolved configuration and return diagnostics."""
    diagnostics: list[ConfigDiagnostic] = []

    if not config.model_ref and not config.model_endpoint:
        diagnostics.append(
            ConfigDiagnostic(
                level="warning",
                message="No model reference or endpoint configured.",
                path="model",
            )
        )

    if config.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message=f"Invalid log level: {config.log_level}",
                path="log_level",
            )
        )

    audit_mode = (config.audit_sink or "").strip().lower()
    if audit_mode not in ("", "none", "stdout", "file"):
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message=(f"Invalid audit sink: {config.audit_sink}. Supported: none, stdout, file"),
                path="audit_sink",
            )
        )
    if audit_mode == "file" and not config.audit_file:
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message="MICRO_AGENT_AUDIT_SINK=file requires MICRO_AGENT_AUDIT_FILE",
                path="audit_file",
            )
        )

    auth_mode = (config.auth or "").strip().lower()
    if auth_mode not in ("", "none", "oidc"):
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message=f"Invalid auth mode: {config.auth}. Supported: none, oidc",
                path="auth",
            )
        )
    if auth_mode == "oidc":
        if not config.auth_issuer:
            diagnostics.append(
                ConfigDiagnostic(
                    level="error",
                    message="MICRO_AGENT_AUTH=oidc requires MICRO_AGENT_AUTH_ISSUER",
                    path="auth_issuer",
                )
            )
        if not config.auth_audience:
            diagnostics.append(
                ConfigDiagnostic(
                    level="error",
                    message="MICRO_AGENT_AUTH=oidc requires MICRO_AGENT_AUTH_AUDIENCE",
                    path="auth_audience",
                )
            )

    return diagnostics
