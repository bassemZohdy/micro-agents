"""Unit tests for micro_agent.security.credentials."""

from __future__ import annotations

import httpx
import pytest

from micro_agent.security.credentials import (
    CredentialProvider,
    EnvironmentCredentialProvider,
    StaticCredentialProvider,
    VaultCredentialError,
    VaultCredentialProvider,
)


class TestEnvironmentCredentialProvider:
    def test_resolve_existing_variable(self):
        environ = {"MY_SECRET": "value123"}
        provider = EnvironmentCredentialProvider(environ=environ)
        assert provider.resolve("MY_SECRET") == "value123"

    def test_resolve_missing_variable_returns_none(self):
        environ = {"MY_SECRET": "value123"}
        provider = EnvironmentCredentialProvider(environ=environ)
        assert provider.resolve("NONEXISTENT") is None

    def test_resolve_empty_environ(self):
        provider = EnvironmentCredentialProvider(environ={})
        assert provider.resolve("ANYTHING") is None

    def test_uses_os_environ_when_none(self):
        provider = EnvironmentCredentialProvider(environ=None)
        assert provider._environ is __import__("os").environ

    def test_resolve_empty_reference(self):
        environ = {"": "empty_key_value"}
        provider = EnvironmentCredentialProvider(environ=environ)
        assert provider.resolve("") == "empty_key_value"


class TestStaticCredentialProvider:
    def test_resolve_existing_reference(self):
        provider = StaticCredentialProvider({"api_key": "sk-123"})
        assert provider.resolve("api_key") == "sk-123"

    def test_resolve_missing_reference_returns_none(self):
        provider = StaticCredentialProvider({"api_key": "sk-123"})
        assert provider.resolve("nonexistent") is None

    def test_resolve_empty_values(self):
        provider = StaticCredentialProvider({})
        assert provider.resolve("anything") is None

    def test_repr_redacts_values(self):
        provider = StaticCredentialProvider({"api_key": "sk-123"})
        assert "sk-123" not in repr(provider)
        assert repr(provider) == "StaticCredentialProvider(***)"

    def test_values_are_copied(self):
        original = {"api_key": "sk-123"}
        provider = StaticCredentialProvider(original)
        original["api_key"] = "changed"
        assert provider.resolve("api_key") == "sk-123"


class TestCredentialProviderInterface:
    def test_environment_provider_is_subclass(self):
        assert issubclass(EnvironmentCredentialProvider, CredentialProvider)

    def test_static_provider_is_subclass(self):
        assert issubclass(StaticCredentialProvider, CredentialProvider)

    def test_vault_provider_is_subclass(self):
        assert issubclass(VaultCredentialProvider, CredentialProvider)

    def test_cannot_instantiate_abstract(self):
        try:
            CredentialProvider()
        except TypeError:
            pass
        else:
            raise AssertionError("should not be instantiable")


class TestVaultCredentialProvider:
    def test_resolves_kv_v2_field_without_leaking_token(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"data": {"api_key": "secret-value"}}})

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://vault.test")
        provider = VaultCredentialProvider(
            "https://vault.test", "vault-secret-token", client=client
        )
        try:
            assert provider.resolve("vault://secret/apps/greeter#api_key") == "secret-value"
            assert provider.resolve("MODEL_API_KEY") is None
            assert seen[0].url.path == "/v1/secret/data/apps/greeter"
            assert seen[0].headers["X-Vault-Token"] == "vault-secret-token"
            assert "vault-secret-token" not in repr(provider)
        finally:
            client.close()

    def test_missing_secret_and_malformed_response_are_safe(self):
        responses = [
            httpx.Response(404),
            httpx.Response(200, json={"data": {"metadata": {}}}),
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://vault.test")
        provider = VaultCredentialProvider("https://vault.test", "token", client=client)
        try:
            assert provider.resolve("vault://secret/apps/missing#api_key") is None
            with pytest.raises(VaultCredentialError, match="KV v2 contract"):
                provider.resolve("vault://secret/apps/broken#api_key")
        finally:
            client.close()

    @pytest.mark.parametrize(
        "endpoint",
        ["http://vault.example", "https://user:pass@vault.example", "https://vault.example/v1"],
    )
    def test_endpoint_must_be_safe(self, endpoint):
        with pytest.raises(ValueError, match="Vault"):
            VaultCredentialProvider(endpoint, "token")

    def test_reference_and_token_are_validated(self):
        with pytest.raises(ValueError, match="token"):
            VaultCredentialProvider("https://vault.example", "")
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        provider = VaultCredentialProvider("https://vault.example", "token", client=client)
        try:
            for reference in (
                "vault://secret/apps/missing",
                "vault://secret/../missing#api_key",
                "vault://secret/apps/missing?x=1#api_key",
            ):
                with pytest.raises(VaultCredentialError, match="invalid Vault"):
                    provider.resolve(reference)
        finally:
            client.close()
