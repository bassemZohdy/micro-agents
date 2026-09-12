"""Credential providers: resolve secret references to secret values.

A credential reference travels with definitions and configuration; only a
configured provider turns it into a value, as late as possible. The
environment provider is the built-in default; deployments that keep secrets
out of the environment supply a non-environment provider (pre-loaded secret
file, Vault, Kubernetes Secret) through the bootstrap. Resolved values are
never logged, serialized, or embedded in errors.
"""

from __future__ import annotations

import importlib
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import suppress
from ipaddress import ip_address
from urllib.parse import quote, urlsplit

import httpx


class CredentialProvider(ABC):
    """Resolves credential references to secret values."""

    @abstractmethod
    def resolve(self, reference: str) -> str | None:
        """Return the secret for a reference, or ``None`` when unavailable."""


class EnvironmentCredentialProvider(CredentialProvider):
    """Resolves references from environment variables (built-in default)."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def resolve(self, reference: str) -> str | None:
        return self._environ.get(reference)


class StaticCredentialProvider(CredentialProvider):
    """Resolves references from pre-loaded values (non-environment).

    For deployments that read a mounted secret file or a secret manager
    before runtime creation and pass the mapping in. Values are held
    privately and never appear in ``repr`` or logs.
    """

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values: dict[str, str] = dict(values)

    def resolve(self, reference: str) -> str | None:
        return self._values.get(reference)

    def __repr__(self) -> str:
        return "StaticCredentialProvider(***)"


class VaultCredentialError(RuntimeError):
    """Raised when a Vault credential lookup cannot be completed safely."""


class VaultCredentialProvider(CredentialProvider):
    """Resolve ``vault://mount/path#field`` references from Vault KV v2.

    The provider performs a fresh lookup for every resolution so Vault-side
    rotation is observed at use time. The Vault token is held only in the
    provider and is never included in representations or exception text.
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._endpoint = self._validate_endpoint(endpoint).rstrip("/")
        if not token:
            raise ValueError("Vault token must not be empty")
        if timeout <= 0:
            raise ValueError("Vault timeout must be positive")
        self._token = token
        self._timeout = timeout
        self._client = client or httpx.Client(base_url=self._endpoint, trust_env=False)
        self._owns_client = client is None

    def resolve(self, reference: str) -> str | None:
        """Resolve a Vault reference, returning ``None`` for other schemes."""
        parsed = self._parse_reference(reference)
        if parsed is None:
            return None
        mount, secret_path, field = parsed
        request_path = f"/v1/{quote(mount, safe='')}/data/{quote(secret_path, safe='/')}"
        try:
            response = self._client.get(
                request_path,
                headers={"X-Vault-Token": self._token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise VaultCredentialError("Vault credential lookup failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise VaultCredentialError(
                f"Vault credential lookup returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise VaultCredentialError("Vault credential response was not valid JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise VaultCredentialError("Vault credential response did not match the KV v2 contract")
        values = body["data"].get("data")
        if not isinstance(values, dict):
            raise VaultCredentialError("Vault credential response did not match the KV v2 contract")
        value = values.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise VaultCredentialError("Vault credential field must contain a string")
        return value

    def close(self) -> None:
        """Close the owned HTTP client; injected clients remain host-owned."""
        if self._owns_client:
            self._client.close()

    def __repr__(self) -> str:
        return f"VaultCredentialProvider(endpoint={self._endpoint!r})"

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Vault endpoint must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Vault endpoint must not contain credentials, query, or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("Vault endpoint must not contain a path")
        host = parsed.hostname or ""
        loopback = host.lower() == "localhost"
        with suppress(ValueError):
            loopback = loopback or ip_address(host).is_loopback
        if parsed.scheme == "http" and not loopback:
            raise ValueError("Vault endpoint must use HTTPS outside loopback")
        return endpoint

    @staticmethod
    def _parse_reference(reference: str) -> tuple[str, str, str] | None:
        if not reference.startswith("vault://"):
            return None
        parsed = urlsplit(reference)
        mount = parsed.netloc
        secret_path = parsed.path.lstrip("/")
        field = parsed.fragment
        segments = secret_path.split("/") if secret_path else []
        if (
            parsed.scheme != "vault"
            or not mount
            or not secret_path
            or not field
            or parsed.username
            or parsed.password
            or parsed.query
            or any(not segment or segment in {".", ".."} for segment in segments)
            or "/" in field
        ):
            raise VaultCredentialError("invalid Vault credential reference")
        return mount, secret_path, field


class AwsSecretsManagerCredentialError(RuntimeError):
    """Raised when an AWS Secrets Manager lookup cannot be completed safely."""


class AwsSecretsManagerCredentialProvider(CredentialProvider):
    """Resolve ``aws-secretsmanager://secret-id[#json-field]`` references.

    The provider makes a fresh Secrets Manager lookup for each resolution so
    rotation is observed at use time. AWS SDK construction is lazy and remains
    optional; inject a client for tests or a deployment-owned session.
    """

    def __init__(self, *, region_name: str | None = None, client: object | None = None) -> None:
        self._region_name = region_name
        self._owns_client = client is None
        if client is None:
            try:
                boto3 = importlib.import_module("boto3")
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "AWS Secrets Manager credentials require the 'aws' extra ('micro-agents[aws]')"
                ) from exc
            self._client = boto3.client("secretsmanager", region_name=region_name)
        else:
            self._client = client

    def resolve(self, reference: str) -> str | None:
        """Resolve an AWS Secrets Manager reference, or ignore other schemes."""
        parsed = self._parse_reference(reference)
        if parsed is None:
            return None
        secret_id, field = parsed
        try:
            response = self._client.get_secret_value(SecretId=secret_id)
        except Exception as exc:  # noqa: BLE001 — SDK errors vary by botocore version
            error_response = getattr(exc, "response", {})
            error = error_response.get("Error", {}) if isinstance(error_response, dict) else {}
            if isinstance(error, dict) and error.get("Code") == "ResourceNotFoundException":
                return None
            raise AwsSecretsManagerCredentialError(
                "AWS Secrets Manager credential lookup failed"
            ) from exc
        if not isinstance(response, dict):
            raise AwsSecretsManagerCredentialError(
                "AWS Secrets Manager response did not match its contract"
            )
        secret_value = response.get("SecretString")
        if secret_value is None:
            binary = response.get("SecretBinary")
            if not isinstance(binary, (bytes, bytearray)):
                raise AwsSecretsManagerCredentialError(
                    "AWS Secrets Manager response did not contain a string secret"
                )
            try:
                secret_value = bytes(binary).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AwsSecretsManagerCredentialError(
                    "AWS Secrets Manager binary secret was not UTF-8"
                ) from exc
        if not isinstance(secret_value, str):
            raise AwsSecretsManagerCredentialError(
                "AWS Secrets Manager response did not contain a string secret"
            )
        if field is None:
            return secret_value
        try:
            decoded = json.loads(secret_value)
        except ValueError as exc:
            raise AwsSecretsManagerCredentialError(
                "AWS Secrets Manager secret is not valid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise AwsSecretsManagerCredentialError(
                "AWS Secrets Manager JSON secret must be an object"
            )
        value = decoded.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise AwsSecretsManagerCredentialError(
                "AWS Secrets Manager JSON field must contain a string"
            )
        return value

    def close(self) -> None:
        """Close an owned SDK client; injected clients remain host-owned."""
        if self._owns_client:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()

    def __repr__(self) -> str:
        region = self._region_name or "default"
        return f"AwsSecretsManagerCredentialProvider(region={region!r})"

    @staticmethod
    def _parse_reference(reference: str) -> tuple[str, str | None] | None:
        parsed = urlsplit(reference)
        if parsed.scheme != "aws-secretsmanager":
            return None
        if parsed.username or parsed.password or parsed.query:
            raise AwsSecretsManagerCredentialError(
                "invalid AWS Secrets Manager credential reference"
            )
        secret_id = f"{parsed.netloc}{parsed.path}"
        if not parsed.netloc:
            secret_id = parsed.path.lstrip("/")
        segments = secret_id.split("/")
        field = parsed.fragment or None
        if (
            not secret_id
            or any(not segment or segment in {".", ".."} for segment in segments)
            or (field is not None and (not field or "/" in field))
        ):
            raise AwsSecretsManagerCredentialError(
                "invalid AWS Secrets Manager credential reference"
            )
        return secret_id, field


__all__ = [
    "CredentialProvider",
    "EnvironmentCredentialProvider",
    "AwsSecretsManagerCredentialError",
    "AwsSecretsManagerCredentialProvider",
    "StaticCredentialProvider",
    "VaultCredentialError",
    "VaultCredentialProvider",
]
