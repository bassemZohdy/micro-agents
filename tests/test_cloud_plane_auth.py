"""Authentication boundary tests for the cloud control-plane APIs."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cloud.config import create_config_app
from cloud.gateway import StaticTokenAuthenticator
from cloud.observability import create_observability_app
from cloud.registry import create_registry_app


def test_registry_config_and_observability_planes_share_auth_boundary() -> None:
    authenticator = StaticTokenAuthenticator({"plane-token": ("platform", "operator")})
    planes = (
        (create_registry_app(authenticator=authenticator), "/registry/agents"),
        (create_config_app(authenticator=authenticator), "/config/agents/demo/history"),
        (create_observability_app(authenticator=authenticator), "/observability/topology"),
    )
    for app, protected_path in planes:
        with TestClient(app) as http:
            assert http.get(protected_path).status_code == 401
            assert http.get(
                protected_path,
                headers={"Authorization": "Bearer plane-token"},
            ).status_code in {200, 404}
            assert http.get("/health/ready").status_code == 200
