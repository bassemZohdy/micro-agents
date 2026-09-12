"""Micro-Agent Configuration Framework.

Implements configuration precedence:
    Framework Defaults → Definition → Environment → Secret Bindings
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

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
    idempotency_endpoint: str | None = None
    approval_endpoint: str | None = None
    knowledge_endpoint: str | None = None
    a2a_store_path: str | None = None
    model_catalog_endpoint: str | None = None
    model_catalog_token_ref: SecretRef | None = None
    policy_store_endpoint: str | None = None
    policy_store_token_ref: SecretRef | None = None
    log_level: str | None = None
    auth: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    audit_sink: str | None = None
    audit_file: str | None = None
    audit_database: str | None = None
    audit_retention_seconds: float | None = None
    max_request_bytes: int | None = None
    rate_limit_per_minute: int | None = None
    rate_limit_burst: int | None = None
    cors_origins: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class EnvironmentOverlay(BaseModel, extra="forbid"):
    """Deployment-only endpoint bindings for a portable definition.

    An overlay carries environment-specific locations without copying or
    mutating the logical agent definition. Values are converted into the
    regular :class:`EnvironmentConfig` precedence layer by the bootstrap.
    Credentials, policies, and agent semantics remain outside this object.
    """

    model_endpoint: str | None = None
    mcp_endpoints: dict[str, str] = Field(default_factory=dict)
    memory_endpoint: str | None = None
    session_endpoint: str | None = None
    idempotency_endpoint: str | None = None
    approval_endpoint: str | None = None
    knowledge_endpoint: str | None = None
    a2a_store_path: str | None = None
    model_catalog_endpoint: str | None = None
    policy_store_endpoint: str | None = None

    @classmethod
    def _validate_http_endpoint(cls, value: str, field_name: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{field_name} must be an absolute http(s) URL")
        return value

    @field_validator("model_endpoint")
    @classmethod
    def validate_model_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            return cls._validate_http_endpoint(value, "model_endpoint")
        return value

    @field_validator("policy_store_endpoint")
    @classmethod
    def validate_policy_store_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            return cls._validate_http_endpoint(value, "policy_store_endpoint")
        return value

    @field_validator("model_catalog_endpoint")
    @classmethod
    def validate_model_catalog_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            return cls._validate_http_endpoint(value, "model_catalog_endpoint")
        return value

    @field_validator("mcp_endpoints")
    @classmethod
    def validate_mcp_endpoints(cls, values: dict[str, str]) -> dict[str, str]:
        for ref, endpoint in values.items():
            if not ref or not ref.strip():
                raise ValueError("mcp_endpoints keys must not be empty")
            cls._validate_http_endpoint(endpoint, f"MCP endpoint for '{ref}'")
        return values

    def to_environment_config(self) -> EnvironmentConfig:
        """Convert overlay values into a config layer without in-place edits."""
        return EnvironmentConfig(
            model_endpoint=self.model_endpoint,
            mcp_endpoints=dict(self.mcp_endpoints),
            memory_endpoint=self.memory_endpoint,
            session_endpoint=self.session_endpoint,
            idempotency_endpoint=self.idempotency_endpoint,
            approval_endpoint=self.approval_endpoint,
            knowledge_endpoint=self.knowledge_endpoint,
            a2a_store_path=self.a2a_store_path,
            model_catalog_endpoint=self.model_catalog_endpoint,
            policy_store_endpoint=self.policy_store_endpoint,
        )


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
    idempotency_endpoint: str | None = None
    approval_endpoint: str | None = None
    knowledge_endpoint: str | None = None
    a2a_store_path: str | None = None
    model_catalog_endpoint: str | None = None
    model_catalog_token: str | None = field(default=None, repr=False)
    policy_store_endpoint: str | None = None
    policy_store_token: str | None = None
    log_level: str = "INFO"
    auth: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    audit_sink: str = "stdout"
    audit_file: str | None = None
    audit_database: str | None = None
    audit_retention_seconds: float | None = None
    max_request_bytes: int | None = None
    rate_limit_per_minute: int | None = None
    rate_limit_burst: int | None = None
    cors_origins: list[str] = field(default_factory=list)
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
        if env_config.idempotency_endpoint:
            config.idempotency_endpoint = env_config.idempotency_endpoint
        if env_config.approval_endpoint:
            config.approval_endpoint = env_config.approval_endpoint
        if env_config.knowledge_endpoint:
            config.knowledge_endpoint = env_config.knowledge_endpoint
        if env_config.a2a_store_path:
            config.a2a_store_path = env_config.a2a_store_path
        if env_config.policy_store_endpoint:
            config.policy_store_endpoint = env_config.policy_store_endpoint
        if env_config.model_catalog_endpoint:
            config.model_catalog_endpoint = env_config.model_catalog_endpoint
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
        if env_config.audit_database:
            config.audit_database = env_config.audit_database
        if env_config.audit_retention_seconds is not None:
            config.audit_retention_seconds = env_config.audit_retention_seconds
        if env_config.max_request_bytes is not None:
            config.max_request_bytes = env_config.max_request_bytes
        if env_config.rate_limit_per_minute is not None:
            config.rate_limit_per_minute = env_config.rate_limit_per_minute
        if env_config.rate_limit_burst is not None:
            config.rate_limit_burst = env_config.rate_limit_burst
        if env_config.cors_origins:
            config.cors_origins = list(env_config.cors_origins)
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

    env_idempotency_endpoint = _read_env("idempotency_endpoint")
    if env_idempotency_endpoint:
        config.idempotency_endpoint = env_idempotency_endpoint

    env_approval_endpoint = _read_env("approval_endpoint")
    if env_approval_endpoint:
        config.approval_endpoint = env_approval_endpoint

    env_knowledge_endpoint = _read_env("knowledge_endpoint")
    if env_knowledge_endpoint:
        config.knowledge_endpoint = env_knowledge_endpoint

    env_a2a_store_path = _read_env("a2a_store_path")
    if env_a2a_store_path:
        config.a2a_store_path = env_a2a_store_path

    env_policy_store_endpoint = _read_env("policy_store_endpoint")
    if env_policy_store_endpoint:
        config.policy_store_endpoint = env_policy_store_endpoint

    env_model_catalog_endpoint = _read_env("model_catalog_endpoint")
    if env_model_catalog_endpoint:
        config.model_catalog_endpoint = env_model_catalog_endpoint

    env_model_catalog_token = _read_env("model_catalog_token")
    if env_model_catalog_token:
        config.model_catalog_token = env_model_catalog_token

    env_policy_store_token = _read_env("policy_store_token")
    if env_policy_store_token:
        config.policy_store_token = env_policy_store_token

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

    env_audit_database = _read_env("audit_database")
    if env_audit_database:
        config.audit_database = env_audit_database

    for name in ("max_request_bytes", "rate_limit_per_minute", "rate_limit_burst"):
        raw_value = _read_env(name)
        if raw_value is not None:
            try:
                setattr(config, name, int(raw_value))
            except ValueError as exc:
                raise ValueError(f"{_env_key(name)} must be an integer") from exc

    env_audit_retention = _read_env("audit_retention_seconds")
    if env_audit_retention is not None:
        try:
            config.audit_retention_seconds = float(env_audit_retention)
        except ValueError as exc:
            raise ValueError("MICRO_AGENT_AUDIT_RETENTION_SECONDS must be a number") from exc

    env_cors_origins = _read_env("cors_origins")
    if env_cors_origins is not None:
        config.cors_origins = [origin.strip() for origin in env_cors_origins.split(",")]

    # Layer 4: Secret bindings
    if env_config and env_config.model_api_key_ref:
        config.model_api_key = _resolve_secret(env_config.model_api_key_ref)
    if env_config and env_config.policy_store_token_ref:
        config.policy_store_token = _resolve_secret(env_config.policy_store_token_ref)
    if env_config and env_config.model_catalog_token_ref:
        config.model_catalog_token = _resolve_secret(env_config.model_catalog_token_ref)

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
    if audit_mode not in ("", "none", "stdout", "file", "sqlite"):
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message=(
                    f"Invalid audit sink: {config.audit_sink}. "
                    "Supported: none, stdout, file, sqlite"
                ),
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
    if audit_mode == "sqlite" and not (config.audit_database or config.audit_file):
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message="MICRO_AGENT_AUDIT_SINK=sqlite requires MICRO_AGENT_AUDIT_DATABASE",
                path="audit_database",
            )
        )
    for name in ("max_request_bytes", "rate_limit_per_minute", "rate_limit_burst"):
        value = getattr(config, name)
        if value is not None and value < 1:
            diagnostics.append(
                ConfigDiagnostic(
                    level="error", message=f"{name} must be greater than zero", path=name
                )
            )
    if config.audit_retention_seconds is not None and config.audit_retention_seconds <= 0:
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message="audit_retention_seconds must be greater than zero",
                path="audit_retention_seconds",
            )
        )

    if config.policy_store_endpoint:
        parsed = urlsplit(config.policy_store_endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            diagnostics.append(
                ConfigDiagnostic(
                    level="error",
                    message=(
                        "policy_store_endpoint must be an absolute http(s) URL without "
                        "credentials, query, or fragment"
                    ),
                    path="policy_store_endpoint",
                )
            )

    if config.model_catalog_endpoint:
        parsed = urlsplit(config.model_catalog_endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            diagnostics.append(
                ConfigDiagnostic(
                    level="error",
                    message=(
                        "model_catalog_endpoint must be an absolute http(s) URL without "
                        "credentials, query, or fragment"
                    ),
                    path="model_catalog_endpoint",
                )
            )

    if any(not origin.strip() for origin in config.cors_origins):
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message="MICRO_AGENT_CORS_ORIGINS must not contain empty origins",
                path="cors_origins",
            )
        )
    elif "*" in config.cors_origins and len(config.cors_origins) > 1:
        diagnostics.append(
            ConfigDiagnostic(
                level="error",
                message="MICRO_AGENT_CORS_ORIGINS cannot mix '*' with explicit origins",
                path="cors_origins",
            )
        )
    else:
        for origin in config.cors_origins:
            parsed = urlsplit(origin.strip())
            if origin == "*":
                continue
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                diagnostics.append(
                    ConfigDiagnostic(
                        level="error",
                        message=("CORS origins must be absolute http(s) origins without a path"),
                        path="cors_origins",
                    )
                )
                break

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
