"""Transport authentication tests: OIDC/JWT authenticator and config selection."""

from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from micro_agent.config import BootstrapError, build_authenticator
from micro_agent.config.config import ResolvedConfig
from micro_agent.core import AuthenticationError
from micro_agent.security import OidcJwtAuthenticator

ISSUER = "https://idp.example.test"
AUDIENCE = "micro-agent-api"


def _generate_key() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = "test-key"
    jwk["alg"] = "RS256"
    return private_key, jwk


_PRIVATE_KEY, _JWK = _generate_key()


class FakeJwksClient:
    """Serves one static JWK instead of fetching the issuer's JWKS."""

    def __init__(self, jwk: dict[str, Any]) -> None:
        self._jwk = jwk

    def get_signing_key_from_jwt(self, token: str) -> Any:
        class _Key:
            key = RSAAlgorithm.from_jwk(self._jwk)

        return _Key()


def _authenticator() -> OidcJwtAuthenticator:
    return OidcJwtAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_client=FakeJwksClient(_JWK),
    )


def _token(**claim_overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "exp": now + 300,
        "iat": now,
        "preferred_username": "alice",
        "roles": ["agent-caller"],
    }
    claims.update(claim_overrides)
    for key, value in list(claims.items()):
        if value is None:
            del claims[key]
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-key"})


@pytest.mark.asyncio
async def test_valid_token_yields_verified_user_identity():
    identity = await _authenticator().authenticate({"Authorization": f"Bearer {_token()}"})
    assert identity.caller.caller_id == "user-123"
    assert identity.caller.caller_type == "user"
    assert identity.user is not None
    assert identity.user.user_id == "alice"
    assert identity.user.roles == ["agent-caller"]


@pytest.mark.asyncio
async def test_service_token_maps_to_service_caller_with_tenant():
    token = _token(
        client_id="scheduler-service",
        preferred_username=None,
        tid="tenant-42",
    )
    identity = await _authenticator().authenticate({"Authorization": f"Bearer {token}"})
    assert identity.caller.caller_type == "service"
    assert identity.user is not None
    assert identity.user.tenant_id == "tenant-42"


@pytest.mark.asyncio
async def test_headers_are_matched_case_insensitively():
    identity = await _authenticator().authenticate({"authorization": f"Bearer {_token()}"})
    assert identity.caller.caller_id == "user-123"


@pytest.mark.asyncio
async def test_missing_bearer_header_fails_authentication():
    with pytest.raises(AuthenticationError, match="authentication required"):
        await _authenticator().authenticate({})


@pytest.mark.asyncio
async def test_non_bearer_scheme_fails_authentication():
    with pytest.raises(AuthenticationError, match="authentication required"):
        await _authenticator().authenticate({"Authorization": "Basic dXNlcjpwYXNz"})


@pytest.mark.asyncio
async def test_expired_token_fails_authentication():
    token = _token(exp=int(time.time()) - 600)
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        await _authenticator().authenticate({"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_wrong_audience_fails_authentication():
    token = _token(aud="another-service")
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        await _authenticator().authenticate({"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_wrong_issuer_fails_authentication():
    token = _token(iss="https://evil.example.test")
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        await _authenticator().authenticate({"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_symmetric_token_signature_is_rejected():
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 300},
        "shared-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        await _authenticator().authenticate({"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_garbage_token_fails_authentication():
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        await _authenticator().authenticate({"Authorization": "Bearer not-a-jwt"})


# ---------------------------------------------------------------------------
# Configuration selection
# ---------------------------------------------------------------------------


def test_no_auth_config_builds_no_authenticator():
    assert build_authenticator(ResolvedConfig()) is None
    assert build_authenticator(ResolvedConfig(auth="none")) is None


def test_oidc_auth_requires_issuer_and_audience():
    with pytest.raises(BootstrapError, match="ISSUER"):
        build_authenticator(ResolvedConfig(auth="oidc"))
    with pytest.raises(BootstrapError, match="AUDIENCE"):
        build_authenticator(ResolvedConfig(auth="oidc", auth_issuer=ISSUER))


def test_unknown_auth_mode_fails_configuration():
    with pytest.raises(BootstrapError, match="Unsupported auth mode"):
        build_authenticator(ResolvedConfig(auth="pigeon"))


def test_oidc_authenticator_built_from_config():
    authenticator = build_authenticator(
        ResolvedConfig(auth="oidc", auth_issuer=ISSUER, auth_audience=AUDIENCE)
    )
    assert isinstance(authenticator, OidcJwtAuthenticator)
