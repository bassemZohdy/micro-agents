"""Unit tests for micro_agent.security.credentials."""

from __future__ import annotations

from micro_agent.security.credentials import (
    CredentialProvider,
    EnvironmentCredentialProvider,
    StaticCredentialProvider,
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

    def test_cannot_instantiate_abstract(self):
        try:
            CredentialProvider()
        except TypeError:
            pass
        else:
            raise AssertionError("should not be instantiable")
