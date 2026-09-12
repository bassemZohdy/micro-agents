"""Downstream token delegation for protocol clients.

The agent authenticates an invocation before it reaches this module. A token
exchange provider can turn that verified principal, together with the agent's
own actor credential, into a short-lived credential for one downstream
audience. Raw caller metadata is never accepted as an identity source.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from micro_agent.security.propagation import InvocationIdentity, get_invocation_identity

_INSECURE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class TokenExchangeError(RuntimeError):
    """Raised when a delegated token cannot be issued safely."""


@dataclass(frozen=True)
class DelegatedToken:
    """A short-lived downstream access token."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.access_token, str) or not self.access_token.strip():
            raise ValueError("access_token must not be empty")
        if not isinstance(self.token_type, str) or self.token_type.lower() != "bearer":
            raise ValueError("only Bearer delegated tokens are supported")
        if self.expires_in is not None and (
            isinstance(self.expires_in, bool)
            or not isinstance(self.expires_in, int)
            or self.expires_in < 1
        ):
            raise ValueError("expires_in must be greater than zero")


class TokenExchangeProvider(ABC):
    """Issues a delegated token for a verified invocation and audience."""

    @abstractmethod
    async def exchange(
        self,
        *,
        audience: str,
        actor_token: str | None = None,
        identity: InvocationIdentity | None = None,
    ) -> DelegatedToken:
        """Return a token scoped to ``audience``.

        When ``identity`` is omitted, the provider reads the verified
        invocation identity from the current context. Implementations must
        not infer identity from request metadata or tool arguments.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Release provider resources, if any."""


def _identity_payload(identity: InvocationIdentity | None) -> dict[str, Any] | None:
    if identity is None:
        return None
    # Explicitly select stable principal fields. In particular, do not copy
    # free-form metadata: authenticated claim bags can contain credentials or
    # provider-specific values that must not be delegated downstream.
    return {
        "caller": (
            {
                "caller_id": identity.caller.caller_id,
                "caller_type": identity.caller.caller_type,
            }
            if identity.caller is not None
            else None
        ),
        "user": (
            {
                "user_id": identity.user.user_id,
                "tenant_id": identity.user.tenant_id,
                "roles": list(identity.user.roles),
            }
            if identity.user is not None
            else None
        ),
        "workload": (
            {
                "workload_id": identity.workload.workload_id,
                "namespace": identity.workload.namespace,
                "service_account": identity.workload.service_account,
            }
            if identity.workload is not None
            else None
        ),
    }


def _validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "token exchange endpoint must be an absolute http(s) URL without "
            "credentials, query, or fragment"
        )
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in _INSECURE_LOCAL_HOSTS:
        raise ValueError("token exchange endpoint must use HTTPS outside loopback")
    return endpoint


class HttpTokenExchangeProvider(TokenExchangeProvider):
    """Strict HTTP implementation of the Micro-Agent token-exchange SPI.

    The endpoint accepts an RFC 8693-inspired form request with the verified
    identity in the ``micro_agent_identity`` extension field and returns a
    JSON object containing ``access_token`` and optional ``token_type`` and
    ``expires_in`` fields. The agent credential is sent as an actor token and
    is never included in logs or exception text.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._endpoint = _validate_endpoint(endpoint)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None

    async def exchange(
        self,
        *,
        audience: str,
        actor_token: str | None = None,
        identity: InvocationIdentity | None = None,
    ) -> DelegatedToken:
        if not audience or not audience.strip():
            raise TokenExchangeError("token exchange audience must not be empty")
        verified_identity = identity if identity is not None else get_invocation_identity()
        try:
            identity_json = json.dumps(
                _identity_payload(verified_identity), separators=(",", ":"), sort_keys=True
            )
        except (TypeError, ValueError) as exc:
            raise TokenExchangeError("verified invocation identity is not serializable") from exc
        form: dict[str, str] = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "audience": audience,
            "subject_token_type": "urn:micro-agents:verified-identity",
            "micro_agent_identity": identity_json,
        }
        if actor_token:
            form["actor_token"] = actor_token
            form["actor_token_type"] = "urn:ietf:params:oauth:token-type:access_token"
        try:
            response = await self._client.post(
                self._endpoint,
                data=form,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise TokenExchangeError("token exchange request failed") from exc
        if not isinstance(payload, dict):
            raise TokenExchangeError("token exchange response must be an object")
        allowed = {"access_token", "token_type", "expires_in"}
        if set(payload) - allowed or "access_token" not in payload:
            raise TokenExchangeError("token exchange response has an invalid contract")
        try:
            token = DelegatedToken(
                access_token=payload["access_token"],
                token_type=payload.get("token_type", "Bearer"),
                expires_in=payload.get("expires_in"),
            )
        except (TypeError, ValueError) as exc:
            raise TokenExchangeError("token exchange response has an invalid token") from exc
        return token

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "DelegatedToken",
    "HttpTokenExchangeProvider",
    "TokenExchangeError",
    "TokenExchangeProvider",
]
