"""Precedence, environment parsing and diagnostics for configuration."""

import pytest

from micro_agent.config.config import (
    EnvironmentConfig,
    EnvironmentOverlay,
    ResolvedConfig,
    SecretRef,
    resolve_config,
    validate_config,
)

_STRING_ENV = {
    "runtime": "RUNTIME",
    "model_id": "MODEL_ID",
    "model_provider": "MODEL_PROVIDER",
    "model_endpoint": "MODEL_ENDPOINT",
    "model_api_key": "MODEL_API_KEY",
    "memory_endpoint": "MEMORY_ENDPOINT",
    "session_endpoint": "SESSION_ENDPOINT",
    "idempotency_endpoint": "IDEMPOTENCY_ENDPOINT",
    "approval_endpoint": "APPROVAL_ENDPOINT",
    "knowledge_endpoint": "KNOWLEDGE_ENDPOINT",
    "a2a_store_path": "A2A_STORE_PATH",
    "token_exchange_endpoint": "TOKEN_EXCHANGE_ENDPOINT",
    "token_exchange_token": "TOKEN_EXCHANGE_TOKEN",
    "policy_store_endpoint": "POLICY_STORE_ENDPOINT",
    "policy_store_token": "POLICY_STORE_TOKEN",
    "model_catalog_endpoint": "MODEL_CATALOG_ENDPOINT",
    "model_catalog_token": "MODEL_CATALOG_TOKEN",
    "log_level": "LOG_LEVEL",
    "auth": "AUTH",
    "auth_issuer": "AUTH_ISSUER",
    "auth_audience": "AUTH_AUDIENCE",
    "audit_sink": "AUDIT_SINK",
    "audit_file": "AUDIT_FILE",
    "audit_database": "AUDIT_DATABASE",
}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("MICRO_AGENT_"):
            monkeypatch.delenv(key)


@pytest.mark.parametrize(("field", "suffix"), sorted(_STRING_ENV.items()))
def test_every_string_environment_variable_overrides_the_overlay(monkeypatch, field, suffix):
    monkeypatch.setenv(f"MICRO_AGENT_{suffix}", "from-env")
    overlay_value = {
        "runtime": "overlay",
        "model_id": "overlay",
        "model_provider": "overlay",
    }.get(field)
    env_config = EnvironmentConfig(**({field: overlay_value} if overlay_value else {}))
    assert getattr(resolve_config(env_config=env_config), field) == "from-env"


def test_layers_apply_in_order_defaults_definition_overlay_env_secrets(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_MODEL_ID", "env-model")
    monkeypatch.setenv("SECRET_KEY", "secret-value")
    overlay = EnvironmentConfig(
        runtime="adk",
        model_id="overlay-model",
        model_provider="overlay-provider",
        model_endpoint="https://overlay.example.test",
        mcp_endpoints={"crm": "https://crm.example.test"},
        memory_endpoint="redis://m",
        session_endpoint="redis://s",
        idempotency_endpoint="redis://i",
        approval_endpoint="redis://a",
        knowledge_endpoint="https://k.example.test",
        a2a_store_path="/tmp/a2a.db",
        token_exchange_endpoint="https://tx.example.test",
        policy_store_endpoint="https://policy.example.test",
        model_catalog_endpoint="https://catalog.example.test",
        log_level="WARNING",
        auth="oidc",
        auth_issuer="https://issuer.example.test",
        auth_audience="agents",
        audit_sink="file",
        audit_file="/tmp/audit.log",
        audit_database="/tmp/audit.db",
        audit_retention_seconds=30.0,
        max_request_bytes=2048,
        rate_limit_per_minute=60,
        rate_limit_burst=10,
        cors_origins=["https://app.example.test"],
        extra={"flag": True},
        model_api_key_ref=SecretRef(name="SECRET_KEY", source="env"),
        policy_store_token_ref=SecretRef(name="SECRET_KEY", source="env"),
        model_catalog_token_ref=SecretRef(name="SECRET_KEY", source="env"),
        token_exchange_token_ref=SecretRef(name="SECRET_KEY", source="env"),
    )
    config = resolve_config(
        {"model_ref": "logical", "model_timeout_seconds": 9, "nope": 1}, overlay
    )

    assert config.model_ref == "logical" and config.model_timeout_seconds == 9
    assert config.model_id == "env-model"  # env beats the overlay
    assert (config.runtime, config.model_provider) == ("adk", "overlay-provider")
    assert config.mcp_endpoints == {"crm": "https://crm.example.test"}
    assert config.log_level == "WARNING" and config.auth == "oidc"
    assert config.audit_retention_seconds == 30.0
    assert (config.max_request_bytes, config.rate_limit_per_minute) == (2048, 60)
    assert config.cors_origins == ["https://app.example.test"]
    assert config.extra == {"flag": True}
    # Secret bindings resolve last.
    assert config.model_api_key == config.policy_store_token == "secret-value"
    assert config.model_catalog_token == config.token_exchange_token == "secret-value"


def test_unresolvable_secret_reference_leaves_the_value_unset():
    overlay = EnvironmentConfig(model_api_key_ref=SecretRef(name="DOES_NOT_EXIST", source="env"))
    assert resolve_config(env_config=overlay).model_api_key is None


@pytest.mark.parametrize("name", ["max_request_bytes", "rate_limit_per_minute", "rate_limit_burst"])
def test_integer_environment_variables_are_parsed_and_validated(monkeypatch, name):
    monkeypatch.setenv(f"MICRO_AGENT_{name.upper()}", "42")
    assert getattr(resolve_config(), name) == 42
    monkeypatch.setenv(f"MICRO_AGENT_{name.upper()}", "forty-two")
    with pytest.raises(ValueError, match="must be an integer"):
        resolve_config()


def test_float_and_list_environment_variables(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_AUDIT_RETENTION_SECONDS", "12.5")
    monkeypatch.setenv(
        "MICRO_AGENT_CORS_ORIGINS", " https://a.example.test , https://b.example.test"
    )
    config = resolve_config()
    assert config.audit_retention_seconds == 12.5
    assert config.cors_origins == ["https://a.example.test", "https://b.example.test"]
    monkeypatch.setenv("MICRO_AGENT_AUDIT_RETENTION_SECONDS", "soon")
    with pytest.raises(ValueError, match="must be a number"):
        resolve_config()


def test_secrets_never_appear_in_the_resolved_config_repr():
    config = ResolvedConfig(
        model_api_key="sk-model-secret",
        policy_store_token="policy-secret",
        token_exchange_token="exchange-secret",
        model_catalog_token="catalog-secret",
    )
    text = repr(config)
    for secret in ("sk-model-secret", "policy-secret", "exchange-secret", "catalog-secret"):
        assert secret not in text


@pytest.mark.parametrize(
    "field",
    [
        "model_endpoint",
        "policy_store_endpoint",
        "model_catalog_endpoint",
        "token_exchange_endpoint",
    ],
)
def test_overlay_endpoints_must_be_absolute_http_urls(field):
    EnvironmentOverlay(**{field: "https://ok.example.test"})
    for bad in ("ftp://x.example.test", "not-a-url", "https://"):
        with pytest.raises(ValueError, match="http"):
            EnvironmentOverlay(**{field: bad})
    with pytest.raises(ValueError, match="empty"):
        EnvironmentOverlay(mcp_endpoints={" ": "https://x.example.test"})
    with pytest.raises(ValueError, match="http"):
        EnvironmentOverlay(mcp_endpoints={"crm": "ftp://x"})


def _errors(config: ResolvedConfig) -> dict[str, str]:
    return {d.path: d.message for d in validate_config(config) if d.level == "error"}


def test_a_valid_configuration_produces_no_errors():
    config = ResolvedConfig(model_ref="m", cors_origins=["https://app.example.test"])
    assert _errors(config) == {}


def test_validate_reports_each_invalid_setting():
    config = ResolvedConfig(
        log_level="LOUD",
        audit_sink="telegram",
        max_request_bytes=0,
        rate_limit_per_minute=-1,
        rate_limit_burst=0,
        audit_retention_seconds=0,
        policy_store_endpoint="https://user:pw@policy.example.test",
        model_catalog_endpoint="ftp://catalog.example.test",
        token_exchange_endpoint="https://tx.example.test/?q=1",
    )
    errors = _errors(config)
    assert set(errors) == {
        "log_level",
        "audit_sink",
        "max_request_bytes",
        "rate_limit_per_minute",
        "rate_limit_burst",
        "audit_retention_seconds",
        "policy_store_endpoint",
        "model_catalog_endpoint",
        "token_exchange_endpoint",
    }


def test_validate_warns_without_a_model_and_requires_audit_destinations():
    warnings = [d for d in validate_config(ResolvedConfig()) if d.level == "warning"]
    assert [d.path for d in warnings] == ["model"]
    assert "audit_file" in _errors(ResolvedConfig(model_ref="m", audit_sink="file"))
    assert "audit_database" in _errors(ResolvedConfig(model_ref="m", audit_sink="sqlite"))
    assert _errors(ResolvedConfig(model_ref="m", audit_sink="sqlite", audit_file="/tmp/a.db")) == {}


@pytest.mark.parametrize(
    ("origins", "expected"),
    [
        ([""], True),
        (["*", "https://a.example.test"], True),
        (["https://a.example.test/path"], True),
        (["ftp://a.example.test"], True),
        (["*"], False),
        (["https://a.example.test", "http://localhost:3000"], False),
    ],
)
def test_validate_cors_origins(origins, expected):
    errors = _errors(ResolvedConfig(model_ref="m", cors_origins=origins))
    assert ("cors_origins" in errors) is expected
