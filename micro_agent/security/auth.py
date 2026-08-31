"""Transport authentication: verified identity for inbound invocations.

An :class:`Authenticator` turns transport credentials into verified identity
objects. Verified identity comes only from an Authenticator — never from
caller-supplied request metadata (a source-level guard test enforces that
boundary).

Selection is external configuration: ``MICRO_AGENT_AUTH`` chooses the
implementation. OIDC/OAuth2 Bearer JWT validation is implemented first as the
dominant scheme (issuer, audience, expiry, and signature via JWKS); other
schemes (gateway-forwarded identity, API keys) are later configurations of
the same SPI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from micro_agent.core import AuthenticationError
from micro_agent.security.identity import CallerIdentity, UserContext

_ASYMMETRIC_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
_BEARER_PREFIX = "bearer "
_REQUIRED_CLAIMS = ["exp", "iss", "sub", "aud"]


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Verified identity produced by an Authenticator."""

    caller: CallerIdentity
    user: UserContext | None = None


class Authenticator(ABC):
    """Verifies transport credentials and returns verified identity."""

    @abstractmethod
    async def authenticate(self, headers: Mapping[str, str]) -> AuthenticatedIdentity:
        """Return verified identity, or raise :class:`AuthenticationError`."""


class OidcJwtAuthenticator(Authenticator):
    """Validates OIDC/OAuth2 Bearer JWTs against issuer, audience, and JWKS.

    ``jwks_client`` is an injection seam for tests and deployments that
    already manage key discovery. Tokens must be signed with an asymmetric
    algorithm and carry ``exp``, ``iss``, ``sub``, and ``aud`` claims.
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_client: Any = None,
        leeway_seconds: int = 30,
    ) -> None:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise RuntimeError(
                "OIDC authentication requires the optional 'auth' extra: "
                "pip install 'micro-agents[auth]'"
            ) from exc
        self._jwt = jwt
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._leeway_seconds = leeway_seconds
        self._jwks_client = jwks_client or jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json")

    async def authenticate(self, headers: Mapping[str, str]) -> AuthenticatedIdentity:
        token = self._extract_bearer_token(headers)
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = self._jwt.decode(
                token,
                signing_key.key,
                algorithms=_ASYMMETRIC_ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": _REQUIRED_CLAIMS},
            )
        except self._jwt.PyJWTError as exc:
            raise AuthenticationError("invalid or expired credentials") from exc
        except AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 — key discovery failures are auth failures
            raise AuthenticationError("credentials could not be validated") from exc
        return _identity_from_claims(claims)

    def _extract_bearer_token(self, headers: Mapping[str, str]) -> str:
        for name, value in headers.items():
            if name.lower() == "authorization":
                if value.lower().startswith(_BEARER_PREFIX):
                    return value[len(_BEARER_PREFIX) :].strip()
                break
        raise AuthenticationError("authentication required")


def _identity_from_claims(claims: dict[str, Any]) -> AuthenticatedIdentity:
    """Map standard OIDC claims onto caller and user identity objects.

    Only verified token claims are read; request data is never consulted.
    """
    subject = str(claims.get("sub", ""))
    is_service = bool(claims.get("client_id")) and not (
        claims.get("preferred_username") or claims.get("email")
    )
    caller = CallerIdentity(
        caller_id=subject,
        caller_type="service" if is_service else "user",
    )
    tenant_id = claims.get("tid") or claims.get("tenant_id")
    roles = [str(role) for role in (claims.get("roles") or [])]
    user = UserContext(
        user_id=str(claims.get("preferred_username") or subject),
        tenant_id=str(tenant_id) if tenant_id else None,
        roles=roles,
    )
    return AuthenticatedIdentity(caller=caller, user=user)


__all__ = [
    "AuthenticatedIdentity",
    "Authenticator",
    "OidcJwtAuthenticator",
]
