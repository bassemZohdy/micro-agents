"""Strict-contract edges for the Vault and AWS Secrets Manager providers."""

from __future__ import annotations

import json

import httpx
import pytest

from micro_agent.security.credentials import (
    AwsSecretsManagerCredentialError,
    AwsSecretsManagerCredentialProvider,
    VaultCredentialError,
    VaultCredentialProvider,
)

# ---------------------------------------------------------------------------
# Vault KV v2
# ---------------------------------------------------------------------------


def _vault(handler) -> VaultCredentialProvider:
    client = httpx.Client(
        base_url="https://vault.example.test", transport=httpx.MockTransport(handler)
    )
    return VaultCredentialProvider("https://vault.example.test", "vault-token", client=client)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://vault.example.test",
        "https://",
        "https://user:pw@vault.example.test",
        "https://vault.example.test?x=1",
        "https://vault.example.test#frag",
        "https://vault.example.test/prefix",
        "http://vault.example.test",
    ],
)
def test_vault_endpoint_must_be_a_bare_https_origin(endpoint):
    with pytest.raises(ValueError):
        VaultCredentialProvider(endpoint, "token")


def test_vault_allows_loopback_http_and_validates_token_and_timeout():
    for endpoint in ("http://localhost:8200", "http://127.0.0.1:8200", "http://[::1]:8200"):
        VaultCredentialProvider(endpoint, "token").close()
    with pytest.raises(ValueError, match="token"):
        VaultCredentialProvider("https://vault.example.test", "")
    with pytest.raises(ValueError, match="timeout"):
        VaultCredentialProvider("https://vault.example.test", "t", timeout=0)


@pytest.mark.parametrize(
    "reference",
    [
        "vault://",
        "vault://secret",
        "vault://secret/app",
        "vault://secret/app#",
        "vault://secret/app#a/b",
        "vault://secret/team//app#f",
        "vault://secret/../app#f",
        "vault://secret/./app#f",
        "vault://user:pw@secret/app#f",
        "vault://secret/app?x=1#f",
    ],
)
def test_vault_rejects_malformed_references(reference):
    provider = _vault(lambda request: httpx.Response(500))
    with pytest.raises(VaultCredentialError, match="invalid Vault credential reference"):
        provider.resolve(reference)


def test_vault_ignores_other_schemes_and_sends_token_header():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"data": {"key": "s3cret"}}})

    provider = _vault(handler)
    assert provider.resolve("env://X") is None
    assert provider.resolve("vault://secret/team/app#key") == "s3cret"
    assert seen[0].headers["x-vault-token"] == "vault-token"
    assert seen[0].url.path == "/v1/secret/data/team/app"
    assert "vault-token" not in repr(provider)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403), "HTTP 403"),
        (httpx.Response(200, content=b"not json"), "not valid JSON"),
        (httpx.Response(200, json=["x"]), "KV v2 contract"),
        (httpx.Response(200, json={"data": "x"}), "KV v2 contract"),
        (httpx.Response(200, json={"data": {"data": "x"}}), "KV v2 contract"),
        (httpx.Response(200, json={"data": {"data": {"key": 5}}}), "must contain a string"),
    ],
)
def test_vault_rejects_non_conforming_responses(response, message):
    provider = _vault(lambda request: response)
    with pytest.raises(VaultCredentialError, match=message):
        provider.resolve("vault://secret/app#key")


def test_vault_missing_secret_or_field_resolves_to_none_and_errors_are_wrapped():
    assert _vault(lambda r: httpx.Response(404)).resolve("vault://secret/app#key") is None
    present = _vault(lambda r: httpx.Response(200, json={"data": {"data": {"other": "x"}}}))
    assert present.resolve("vault://secret/app#key") is None

    def boom(request):
        raise httpx.ConnectError("token vault-token leaked?")

    with pytest.raises(VaultCredentialError) as info:
        _vault(boom).resolve("vault://secret/app#key")
    assert "vault-token" not in str(info.value)


def test_vault_close_only_closes_owned_clients():
    owned = VaultCredentialProvider("https://vault.example.test", "t")
    owned.close()
    assert owned._client.is_closed

    client = httpx.Client()
    injected = VaultCredentialProvider("https://vault.example.test", "t", client=client)
    injected.close()
    assert not client.is_closed
    client.close()


# ---------------------------------------------------------------------------
# AWS Secrets Manager
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[str] = []
        self.closed = False

    def get_secret_value(self, *, SecretId):  # noqa: N803 - boto3 keyword
        self.calls.append(SecretId)
        if self._error is not None:
            raise self._error
        return self._response

    def close(self):
        self.closed = True


class _AwsError(Exception):
    def __init__(self, code: str | None):
        super().__init__("aws failure")
        self.response = {"Error": {"Code": code}} if code else {}


def _aws(response=None, error=None) -> tuple[AwsSecretsManagerCredentialProvider, _Client]:
    client = _Client(response, error)
    return AwsSecretsManagerCredentialProvider(client=client), client


@pytest.mark.parametrize(
    "reference",
    [
        "aws-secretsmanager://",
        "aws-secretsmanager://user:pw@prod/db",
        "aws-secretsmanager://prod/db?x=1",
        "aws-secretsmanager://prod//db",
        "aws-secretsmanager://prod/../db",
        "aws-secretsmanager://prod/db#a/b",
    ],
)
def test_aws_rejects_malformed_references(reference):
    provider, client = _aws({"SecretString": "x"})
    with pytest.raises(AwsSecretsManagerCredentialError, match="invalid"):
        provider.resolve(reference)
    assert client.calls == []


def test_aws_plain_and_json_field_resolution():
    provider, client = _aws({"SecretString": json.dumps({"password": "pw", "n": 1, "z": None})})
    assert provider.resolve("env://X") is None
    assert provider.resolve("aws-secretsmanager://prod/db#password") == "pw"
    assert provider.resolve("aws-secretsmanager://prod/db#missing") is None
    assert provider.resolve("aws-secretsmanager://prod/db#z") is None
    assert client.calls == ["prod/db", "prod/db", "prod/db"]

    plain, _ = _aws({"SecretString": "raw-secret"})
    assert plain.resolve("aws-secretsmanager://api-key") == "raw-secret"
    assert plain.resolve("aws-secretsmanager:///leading/path") == "raw-secret"


@pytest.mark.parametrize(
    ("response", "reference", "message"),
    [
        ("not-a-dict", "aws-secretsmanager://s", "contract"),
        ({}, "aws-secretsmanager://s", "string secret"),
        ({"SecretBinary": "text"}, "aws-secretsmanager://s", "string secret"),
        ({"SecretBinary": b"\xff\xfe"}, "aws-secretsmanager://s", "not UTF-8"),
        ({"SecretString": 5}, "aws-secretsmanager://s", "string secret"),
        ({"SecretString": "not json"}, "aws-secretsmanager://s#f", "not valid JSON"),
        ({"SecretString": "[1]"}, "aws-secretsmanager://s#f", "must be an object"),
        ({"SecretString": '{"f": 5}'}, "aws-secretsmanager://s#f", "must contain a string"),
    ],
)
def test_aws_rejects_non_conforming_secrets(response, reference, message):
    provider, _ = _aws(response)
    with pytest.raises(AwsSecretsManagerCredentialError, match=message):
        provider.resolve(reference)


def test_aws_binary_secret_is_decoded_as_utf8():
    provider, _ = _aws({"SecretBinary": bytearray("päss".encode())})
    assert provider.resolve("aws-secretsmanager://s") == "päss"


def test_aws_not_found_is_none_other_errors_are_wrapped_without_leaking():
    missing, _ = _aws(error=_AwsError("ResourceNotFoundException"))
    assert missing.resolve("aws-secretsmanager://s") is None

    for error in (_AwsError("AccessDeniedException"), _AwsError(None), RuntimeError("boom")):
        provider, _ = _aws(error=error)
        with pytest.raises(AwsSecretsManagerCredentialError, match="lookup failed"):
            provider.resolve("aws-secretsmanager://s")


def test_aws_close_and_repr():
    owned_client = _Client({"SecretString": "x"})
    provider = AwsSecretsManagerCredentialProvider(region_name="eu-west-1", client=owned_client)
    assert "eu-west-1" in repr(provider)
    provider.close()  # injected clients are never closed
    assert owned_client.closed is False

    provider._owns_client = True
    provider.close()
    assert owned_client.closed is True
    assert "default" in repr(AwsSecretsManagerCredentialProvider(client=_Client()))
