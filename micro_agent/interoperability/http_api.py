"""Micro-Agent HTTP API — FastAPI application.

Exposes a Micro-Agent as an independent network service.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from micro_agent.core import (
    AgentRequest,
    AuthenticationError,
    AuthorizationError,
    DefaultMicroAgent,
    DependencyUnavailableError,
    InvocationOverloadedError,
)
from micro_agent.definition import ContractValidationError
from micro_agent.interoperability.a2a import (
    a2a_well_known_path,
    agent_card_from_definition,
)
from micro_agent.observability import HealthChecker, Telemetry
from micro_agent.security.auth import AuthenticatedIdentity, Authenticator

# ---------------------------------------------------------------------------
# API Models (Pydantic for FastAPI)
# ---------------------------------------------------------------------------


class InvokeRequestModel(BaseModel):
    """HTTP invocation request."""

    input: dict[str, Any] = {}
    request_id: str | None = None
    session_id: str | None = None
    caller_metadata: dict[str, Any] = {}
    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Optional end-to-end invocation deadline in seconds.",
    )


class InvokeResponseModel(BaseModel):
    """HTTP invocation response."""

    output: dict[str, Any] = {}
    request_id: str = ""
    session_id: str | None = None
    status: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = {}


class HealthResponseModel(BaseModel):
    """Health check response."""

    status: str = "healthy"
    details: dict[str, Any] = {}


class CapabilitiesResponseModel(BaseModel):
    """Capabilities response."""

    agent_name: str = ""
    agent_version: str = ""
    skills: list[dict[str, Any]] = []
    capabilities: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Legacy dataclass models (kept for backward compat)
# ---------------------------------------------------------------------------


@dataclass
class InvokeRequest:
    """HTTP invocation request."""

    input: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    session_id: str | None = None
    caller_metadata: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None


@dataclass
class InvokeResponse:
    """HTTP invocation response."""

    output: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: str | None = None
    status: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthResponse:
    """Health check response."""

    status: str = "healthy"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilitiesResponse:
    """Capabilities response."""

    agent_name: str = ""
    agent_version: str = ""
    skills: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Route Definitions
# ---------------------------------------------------------------------------


ROUTES = {
    "POST /v1/invoke": "Invoke the agent",
    "GET /health/live": "Liveness check",
    "GET /health/ready": "Readiness check",
    "GET /v1/capabilities": "Agent capabilities",
}

DEFAULT_MAX_REQUEST_BYTES = 1_048_576

# Routes that require a verified caller identity when an authenticator is
# configured. Health probes and the A2A discovery card stay public by design.
AUTHENTICATED_PATHS = frozenset({"/v1/invoke"})


def serialize_response(data: Any) -> str:
    """Serialize a response to JSON, handling nested dataclasses."""
    if hasattr(data, "__dataclass_fields__"):
        return json.dumps(asdict(data), default=str)
    if hasattr(data, "model_dump"):
        return json.dumps(data.model_dump(), default=str)
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# FastAPI Application Factory
# ---------------------------------------------------------------------------


def create_app(
    agent: DefaultMicroAgent,
    health_checker: HealthChecker | None = None,
    telemetry: Telemetry | None = None,
    base_url: str | None = None,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    authenticator: Authenticator | None = None,
) -> FastAPI:
    """Create a FastAPI application for a Micro-Agent.

    ``authenticator`` verifies caller credentials on authenticated routes;
    when the definition demands caller identity but none is configured, app
    creation fails instead of silently serving unverified callers.
    """
    if max_request_bytes < 1:
        raise ValueError("max_request_bytes must be greater than zero")

    identity_requirements = agent.definition.spec.security.identity_requirements
    if identity_requirements.get("require_caller_identity") and authenticator is None:
        raise RuntimeError(
            "definition requires caller identity "
            "(security.identity_requirements.require_caller_identity) but no "
            "authenticator is configured; set MICRO_AGENT_AUTH=oidc with "
            "MICRO_AGENT_AUTH_ISSUER and MICRO_AGENT_AUTH_AUDIENCE"
        )

    app = FastAPI(
        title=agent.definition.metadata.name,
        version=agent.definition.metadata.version,
        description=agent.definition.metadata.description or "",
    )
    checker = health_checker or HealthChecker()
    telemetry = telemetry or Telemetry()
    telemetry.logger.set_context(
        agent_id=agent.identity.agent_id,
        agent_version=agent.identity.agent_version,
    )

    agent_card = agent_card_from_definition(agent.definition, base_url=base_url)

    @app.middleware("http")
    async def authenticate_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Verify caller credentials before any authenticated route runs.

        Verified identity is stored on the request state and attached to the
        invocation; unauthenticated calls fail before the agent is reached.
        """
        if authenticator is None or request.url.path not in AUTHENTICATED_PATHS:
            return await call_next(request)
        try:
            identity: AuthenticatedIdentity = await authenticator.authenticate(request.headers)
        except AuthenticationError:
            telemetry.increment("http_auth_failures_total", {"route": request.url.path})
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "code": "authentication_required",
                    "message": "Authentication required",
                },
            )
        request.state.identity = identity
        telemetry.logger.info(
            "caller authenticated",
            route=request.url.path,
            caller_id=identity.caller.caller_id,
            caller_type=identity.caller.caller_type,
        )
        return await call_next(request)

    @app.middleware("http")
    async def enforce_request_size(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject oversized requests before FastAPI parses their JSON body.

        Content-Length is checked before parsing. Clients using chunked transfer
        should send a length header when operating behind a gateway that
        enforces the same limit; deployment guidance requires that gateway guard.
        """
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"code": "invalid_content_length", "message": "Invalid Content-Length"},
                )
            if content_length > max_request_bytes:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={
                        "code": "request_too_large",
                        "message": f"Request body exceeds {max_request_bytes} bytes",
                    },
                )
        return await call_next(request)

    @app.get(a2a_well_known_path(), response_model=None)
    async def get_agent_card() -> dict[str, Any]:
        """A2A well-known agent card for discovery."""
        telemetry.increment("http_requests_total", {"route": "/.well-known/agent.json"})
        return asdict(agent_card)

    @app.post("/v1/invoke", response_model=InvokeResponseModel)
    async def invoke(request: InvokeRequestModel, http_request: Request) -> InvokeResponseModel:
        telemetry.increment("http_requests_total", {"route": "/v1/invoke", "method": "POST"})
        identity: AuthenticatedIdentity | None = getattr(http_request.state, "identity", None)
        agent_request = AgentRequest(
            input=request.input,
            session_id=request.session_id,
            caller_metadata=request.caller_metadata,
            timeout_seconds=request.timeout_seconds,
            caller_identity=identity.caller if identity else None,
            user_context=identity.user if identity else None,
        )
        if request.request_id:
            agent_request.request_id = request.request_id
        telemetry.logger.info(
            "invoke request",
            request_id=agent_request.request_id,
            session_id=request.session_id,
            authenticated=identity is not None,
        )
        try:
            response = await agent.invoke(agent_request)
        except InvocationOverloadedError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": "1"},
                detail={"code": "invocation_overloaded", "limit": exc.limit},
            ) from exc
        except ContractValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "contract_validation_failed",
                    "contract": exc.contract,
                    "errors": exc.errors,
                },
            ) from exc
        except TimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={"code": "deadline_exceeded", "message": "Invocation deadline exceeded"},
            ) from exc
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
                detail={
                    "code": "authentication_required",
                    "message": "Authentication required",
                },
            ) from exc
        except (AuthorizationError, PermissionError) as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "authorization_denied", "message": "Authorization denied"},
            ) from exc
        except (DependencyUnavailableError, ConnectionError, httpx.RequestError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "dependency_unavailable",
                    "message": "Required dependency unavailable",
                },
            ) from exc
        except Exception as exc:  # noqa: BLE001 — stable public error contract
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "internal_error", "message": "Internal server error"},
            ) from exc
        telemetry.logger.info(
            "invoke completed",
            request_id=agent_request.request_id,
            status=response.status,
        )
        return InvokeResponseModel(
            output=response.output,
            request_id=response.request_id,
            session_id=response.session_id,
            status=response.status,
            error=response.error,
            metadata=response.metadata,
        )

    @app.get("/health/live", response_model=HealthResponseModel)
    async def liveness() -> HealthResponseModel:
        result = checker.check_liveness()
        return HealthResponseModel(
            status="healthy" if result.alive else "unhealthy",
            details=result.details,
        )

    @app.get("/health/ready", response_model=HealthResponseModel)
    async def readiness(response: Response) -> HealthResponseModel:
        result = await checker.probe_readiness()
        if not result.is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponseModel(
            status=result.status.value,
            details={
                "ready": result.is_ready,
                "dependencies": [
                    {"name": d.name, "status": d.status.value} for d in result.dependencies
                ],
            },
        )

    @app.get("/v1/capabilities", response_model=CapabilitiesResponseModel)
    async def capabilities() -> CapabilitiesResponseModel:
        caps = agent.runtime_capabilities
        skills = [
            {"id": s.id, "name": s.name, "description": s.description}
            for s in agent.definition.spec.dependencies.skills
        ]
        return CapabilitiesResponseModel(
            agent_name=agent.identity.agent_name,
            agent_version=agent.identity.agent_version,
            skills=skills,
            capabilities=caps.as_dict(),
        )

    return app
