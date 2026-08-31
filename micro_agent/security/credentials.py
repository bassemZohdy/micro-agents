"""Credential providers: resolve secret references to secret values.

A credential reference travels with definitions and configuration; only a
configured provider turns it into a value, as late as possible. The
environment provider is the built-in default; deployments that keep secrets
out of the environment supply a non-environment provider (pre-loaded secret
file, Vault, Kubernetes Secret) through the bootstrap. Resolved values are
never logged, serialized, or embedded in errors.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping


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


__all__ = [
    "CredentialProvider",
    "EnvironmentCredentialProvider",
    "StaticCredentialProvider",
]
