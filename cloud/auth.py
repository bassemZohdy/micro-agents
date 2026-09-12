"""Authentication helpers shared by the cloud control-plane APIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response


class PlaneAuthenticator(Protocol):
    """Synchronous header-to-verified-caller contract for cloud planes."""

    def authenticate(self, headers: Any) -> Any | None:
        """Return a verified caller, or ``None`` for invalid credentials."""


def install_plane_auth(app: FastAPI, authenticator: PlaneAuthenticator | None) -> None:
    """Protect every control-plane route except readiness.

    Authentication is opt-in to preserve the reference apps' local testing
    ergonomics. Deployments should pass a static or OIDC authenticator; health
    checks remain public for orchestrator probes.
    """

    if authenticator is None:
        return

    @app.middleware("http")
    async def authenticate_plane_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path != "/health/ready":
            caller = authenticator.authenticate(request.headers)
            if caller is None:
                return JSONResponse(
                    {"detail": "missing or invalid bearer credentials"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)


__all__ = ["PlaneAuthenticator", "install_plane_auth"]
