"""Tests for Micro-Agent Configuration Framework."""

import pytest

from micro_agent.config import (
    EnvironmentConfig,
    EnvironmentOverlay,
    ResolvedConfig,
    SecretRef,
    resolve_config,
    validate_config,
)


class TestResolveConfig:
    """Test configuration resolution with precedence."""

    def test_defaults_only(self):
        config = resolve_config()
        assert config.runtime is None
        assert config.log_level == "INFO"
        assert config.model_endpoint is None
        assert config.mcp_endpoints == {}

    def test_definition_overrides(self):
        config = resolve_config(
            definition_overrides={
                "model_ref": "test-model",
                "model_provider": "test-provider",
            }
        )
        assert config.model_ref == "test-model"
        assert config.model_provider == "test-provider"

    def test_env_config_overrides_definition(self):
        config = resolve_config(
            definition_overrides={"model_endpoint": "https://default.example.com"},
            env_config=EnvironmentConfig(model_endpoint="https://override.example.com"),
        )
        assert config.model_endpoint == "https://override.example.com"

    def test_environment_variable_override(self, monkeypatch):
        monkeypatch.setenv("MICRO_AGENT_MODEL_ENDPOINT", "https://env.example.com")
        config = resolve_config()
        assert config.model_endpoint == "https://env.example.com"

    def test_environment_log_level_override(self, monkeypatch):
        monkeypatch.setenv("MICRO_AGENT_LOG_LEVEL", "DEBUG")
        config = resolve_config()
        assert config.log_level == "DEBUG"

    def test_environment_runtime_override(self, monkeypatch):
        monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
        config = resolve_config()
        assert config.runtime == "google-adk"

    def test_secret_resolution(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "secret-value")
        config = resolve_config(
            env_config=EnvironmentConfig(
                model_api_key_ref=SecretRef(name="MY_API_KEY", source="env")
            )
        )
        assert config.model_api_key == "secret-value"

    def test_mcp_endpoints_merged(self):
        config = resolve_config(
            env_config=EnvironmentConfig(mcp_endpoints={"server-a": "https://a.example.com"})
        )
        assert config.mcp_endpoints["server-a"] == "https://a.example.com"


class TestValidateConfig:
    """Test configuration validation."""

    def test_valid_config_no_warnings(self):
        config = ResolvedConfig(model_ref="test-model")
        diagnostics = validate_config(config)
        assert len(diagnostics) == 0

    def test_missing_model_warns(self):
        config = ResolvedConfig()
        diagnostics = validate_config(config)
        assert len(diagnostics) == 1
        assert diagnostics[0].level == "warning"
        assert "model" in diagnostics[0].message.lower()

    def test_invalid_log_level_errors(self):
        config = ResolvedConfig(log_level="INVALID")
        diagnostics = validate_config(config)
        assert any(d.level == "error" for d in diagnostics)

    def test_valid_auth_modes_pass(self):
        assert not any(d.level == "error" for d in validate_config(ResolvedConfig(auth="none")))
        oidc = validate_config(
            ResolvedConfig(auth="oidc", auth_issuer="https://idp", auth_audience="api")
        )
        assert not any(d.level == "error" for d in oidc)

    def test_unknown_auth_mode_errors(self):
        diagnostics = validate_config(ResolvedConfig(auth="saml"))
        assert any(d.level == "error" and "auth" in d.path for d in diagnostics)

    def test_oidc_requires_issuer_and_audience(self):
        missing_issuer = validate_config(ResolvedConfig(auth="oidc", auth_audience="api"))
        assert any("ISSUER" in d.message for d in missing_issuer)
        missing_audience = validate_config(ResolvedConfig(auth="oidc", auth_issuer="https://i"))
        assert any("AUDIENCE" in d.message for d in missing_audience)

    def test_environment_auth_overrides(self, monkeypatch):
        monkeypatch.setenv("MICRO_AGENT_AUTH", "oidc")
        monkeypatch.setenv("MICRO_AGENT_AUTH_ISSUER", "https://idp.example.test")
        monkeypatch.setenv("MICRO_AGENT_AUTH_AUDIENCE", "micro-agent-api")
        config = resolve_config()
        assert config.auth == "oidc"
        assert config.auth_issuer == "https://idp.example.test"
        assert config.auth_audience == "micro-agent-api"


class TestSecretRef:
    """Test secret reference model."""

    def test_secret_ref_creation(self):
        ref = SecretRef(name="MY_SECRET", source="env")
        assert ref.name == "MY_SECRET"
        assert ref.source == "env"

    def test_secret_ref_default_source(self):
        ref = SecretRef(name="MY_SECRET")
        assert ref.source is None


class TestEnvironmentConfig:
    """Test environment configuration model."""

    def test_defaults(self):
        assert EnvironmentConfig().runtime is None
        config = EnvironmentConfig()
        assert config.log_level is None
        assert config.mcp_endpoints == {}
        assert config.extra == {}

    def test_with_values(self):
        config = EnvironmentConfig(
            model_endpoint="https://model.example.com",
            log_level="DEBUG",
        )
        assert config.model_endpoint == "https://model.example.com"
        assert config.log_level == "DEBUG"


class TestEnvironmentOverlay:
    """Deployment endpoint bindings stay separate from logical definitions."""

    def test_overlay_converts_to_environment_config(self):
        overlay = EnvironmentOverlay(
            model_endpoint="https://staging-model.example.com/v1",
            mcp_endpoints={"rules": "https://staging-mcp.example.com"},
            memory_endpoint="memory://",
            session_endpoint="sqlite:///tmp/staging.db",
        )
        config = overlay.to_environment_config()
        assert config.model_endpoint == "https://staging-model.example.com/v1"
        assert config.mcp_endpoints == {"rules": "https://staging-mcp.example.com"}
        assert config.memory_endpoint == "memory://"
        assert config.session_endpoint == "sqlite:///tmp/staging.db"

    def test_overlay_rejects_non_http_endpoints(self):
        with pytest.raises(ValueError, match=r"absolute http\(s\) URL"):
            EnvironmentOverlay(model_endpoint="memory://model")
        with pytest.raises(ValueError, match=r"absolute http\(s\) URL"):
            EnvironmentOverlay(mcp_endpoints={"rules": "localhost:9000"})
