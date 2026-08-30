"""Tests for Micro-Agent Configuration Framework."""

from micro_agent.config import (
    EnvironmentConfig,
    ResolvedConfig,
    SecretRef,
    resolve_config,
    validate_config,
)


class TestResolveConfig:
    """Test configuration resolution with precedence."""

    def test_defaults_only(self):
        config = resolve_config()
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
