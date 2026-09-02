"""Micro-Agent HTTP API — FastAPI application.

Exposes a Micro-Agent as an independent network service.
"""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from micro_agent.core import (
    AgentRequest,
    AuthenticationError,
    AuthorizationError,
    CheckpointNotFoundError,
    ContinuationNotFoundError,
    DefaultMicroAgent,
    DependencyUnavailableError,
    InvocationOverloadedError,
)
from micro_agent.definition import ContractValidationError
from micro_agent.interoperability.a2a import A2aSdkUnavailableError
from micro_agent.observability import AuditSink, HealthChecker, Telemetry
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
    continuation_id: str | None = Field(
        default=None,
        description="Approval continuation id from an approval_required response.",
    )
    approval_decision: Literal["approve", "deny"] | None = None
    checkpoint_id: str | None = Field(
        default=None,
        description=(
            "Replay-safe checkpoint id to resume; do not combine with approval continuation."
        ),
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
    continuation_id: str | None = None
    approval_decision: str | None = None
    checkpoint_id: str | None = None


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
    "GET /metrics": "Prometheus operational metrics",
    "GET /health/live": "Liveness check",
    "GET /health/ready": "Readiness check",
    "GET /v1/capabilities": "Agent capabilities",
}

API_VERSION = "v1"
API_VERSION_HEADER = "X-Micro-Agent-API-Version"
STREAMING_MEDIA_TYPE = "text/event-stream"
DEFAULT_MAX_REQUEST_BYTES = 1_048_576

# Routes that require a verified caller identity when an authenticator is
# configured. Health probes and the A2A discovery card stay public by design.
AUTHENTICATED_PATHS = frozenset({"/v1/invoke"})


@dataclass(frozen=True)
class RateLimitDecision:
    """Result returned by an injected HTTP rate limiter.

    ``retry_after_seconds`` is exposed as ``Retry-After`` when a request is
    rejected.  ``limit`` and ``remaining`` are optional hints for clients;
    the framework does not implement a storage or windowing algorithm.
    """

    allowed: bool
    retry_after_seconds: int = 1
    limit: int | None = None
    remaining: int | None = None

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be non-negative")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be greater than zero")
        if self.remaining is not None and self.remaining < 0:
            raise ValueError("remaining must be non-negative")


RateLimitCheckResult = RateLimitDecision | bool | Awaitable[RateLimitDecision | bool]


class RateLimiter(Protocol):
    """Optional request-level rate limiting integration point.

    Implementations may be synchronous or asynchronous and should key limits
    using verified request identity where available.  No default limiter is
    installed, so deployments opt in to their chosen distributed policy.
    """

    def check(self, request: Request) -> RateLimitCheckResult:
        """Return whether this request may proceed."""


def _validate_cors_origins(origins: Sequence[str] | None) -> list[str]:
    """Validate and normalize an explicit CORS allowlist."""
    if not origins:
        return []
    normalized = [origin.strip() for origin in origins]
    if any(not origin for origin in normalized):
        raise ValueError("cors_origins must not contain empty origins")
    if "*" in normalized and len(normalized) > 1:
        raise ValueError("cors_origins cannot mix '*' with explicit origins")
    if "*" in normalized:
        return normalized
    from urllib.parse import urlsplit

    for origin in normalized:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("cors_origins entries must be absolute http(s) origins without a path")
    return normalized


async def _resolve_rate_limit(result: RateLimitCheckResult) -> RateLimitDecision:
    """Normalize sync/async limiter responses into a validated decision."""
    value: Any = result
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, bool):
        return RateLimitDecision(allowed=value)
    if not isinstance(value, RateLimitDecision):
        raise TypeError("rate limiter must return bool or RateLimitDecision")
    return value


def _accepts_streaming(request: Request) -> bool:
    """Return whether the client explicitly requests an event stream."""
    accept = request.headers.get("accept", "")
    for media_range in accept.split(","):
        parts = [part.strip() for part in media_range.split(";")]
        if not parts or parts[0].lower() != STREAMING_MEDIA_TYPE:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def _sse(name: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, default=str, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


async def _stream_invoke(
    agent: DefaultMicroAgent, request: AgentRequest, telemetry: Telemetry
) -> AsyncIterator[str]:
    """Translate runtime-neutral stream events to the public SSE contract."""
    try:
        async for event in agent.stream(request):
            if event.delta:
                yield _sse("delta", {"delta": event.delta})
            if event.response is not None:
                response = event.response
                telemetry.logger.info(
                    "invoke stream completed",
                    request_id=request.request_id,
                    status=response.status,
                )
                yield _sse(
                    "final",
                    InvokeResponseModel(
                        output=response.output,
                        request_id=response.request_id,
                        session_id=response.session_id,
                        status=response.status,
                        error=response.error,
                        metadata=response.metadata,
                    ).model_dump(),
                )
    except Exception:
        telemetry.increment("http_streaming_errors_total", {"route": "/v1/invoke"})
        yield _sse(
            "error",
            {"code": "stream_failed", "message": "Streaming invocation failed"},
        )


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
    audit_sink: AuditSink | None = None,
    cors_origins: Sequence[str] | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Create a FastAPI application for a Micro-Agent.

    ``authenticator`` verifies caller credentials on authenticated routes;
    when the definition demands caller identity but none is configured, app
    creation fails instead of silently serving unverified callers.
    """
    if max_request_bytes < 1:
        raise ValueError("max_request_bytes must be greater than zero")
    allowed_cors_origins = _validate_cors_origins(cors_origins)

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
        openapi_url=f"/{API_VERSION}/openapi.json",
        docs_url=f"/{API_VERSION}/docs",
        redoc_url=f"/{API_VERSION}/redoc",
    )
    if allowed_cors_origins:
        from starlette.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-A2A-Version",
                "X-Request-ID",
                "traceparent",
                "tracestate",
            ],
            expose_headers=[API_VERSION_HEADER, "Retry-After", "traceparent", "tracestate"],
        )
    checker = health_checker or HealthChecker()
    telemetry = telemetry or Telemetry.from_environment()
    telemetry.logger.set_context(
        agent_id=agent.identity.agent_id,
        agent_version=agent.identity.agent_version,
    )

    # A2A: the standard agent card is served whenever the official SDK is
    # installed; the JSON-RPC task transport requires the definition to
    # enable it, and fails fast when enabled without the SDK.
    a2a_config = agent.definition.spec.interoperability.a2a
    a2a_paths: dict[str, str] = {}
    try:
        from micro_agent.interoperability.a2a_server import attach_a2a

        a2a_paths = attach_a2a(
            app,
            agent,
            base_url=base_url,
            security_scheme=authenticator.security_scheme() if authenticator else None,
            enable_rpc=bool(a2a_config.enabled),
        )
    except A2aSdkUnavailableError:
        if a2a_config.enabled:
            raise
        # Discovery-only A2A stays optional when the definition does not
        # enable the transport and the SDK is not installed.

    identity_requirements = agent.definition.spec.security.identity_requirements
    authenticated_paths = set(AUTHENTICATED_PATHS)
    if a2a_paths.get("rpc") and identity_requirements.get("require_caller_identity"):
        # A2A interactions are guarded by the same transport authentication.
        authenticated_paths.add(a2a_paths["rpc"])

    @app.middleware("http")
    async def guard_a2a_protocol_version(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject A2A requests that declare an unsupported protocol version."""
        rpc_url = a2a_paths.get("rpc")
        if rpc_url is None or request.url.path != rpc_url:
            return await call_next(request)
        declared = request.headers.get("x-a2a-version")
        if declared and declared != a2a_paths.get("protocol_version"):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "code": "unsupported_protocol_version",
                    "message": f"Protocol version '{declared}' is not supported",
                },
            )
        return await call_next(request)

    @app.middleware("http")
    async def authenticate_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Verify caller credentials before any authenticated route runs.

        Verified identity is stored on the request state and attached to the
        invocation; unauthenticated calls fail before the agent is reached.
        """
        if authenticator is None or request.url.path not in authenticated_paths:
            return await call_next(request)
        try:
            identity: AuthenticatedIdentity = await authenticator.authenticate(request.headers)
        except AuthenticationError as exc:
            telemetry.increment("http_auth_failures_total", {"route": request.url.path})
            if audit_sink is not None:
                audit_sink.record(
                    "auth.failure", route=request.url.path, reason=str(exc) or "rejected"
                )
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

    @app.middleware("http")
    async def add_api_version_header(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Advertise the wire API version on every response."""
        response = await call_next(request)
        response.headers[API_VERSION_HEADER] = API_VERSION
        return response

    @app.post("/v1/invoke", response_model=InvokeResponseModel)
    async def invoke(request: InvokeRequestModel, http_request: Request) -> Any:
        telemetry.increment("http_requests_total", {"route": "/v1/invoke", "method": "POST"})
        identity: AuthenticatedIdentity | None = getattr(http_request.state, "identity", None)
        if _accepts_streaming(http_request) and not agent.runtime_capabilities.streaming:
            telemetry.increment("http_streaming_rejections_total", {"route": "/v1/invoke"})
            raise HTTPException(
                status_code=status.HTTP_406_NOT_ACCEPTABLE,
                detail={
                    "code": "streaming_unsupported",
                    "message": "Streaming is not supported by this runtime",
                },
            )
        if rate_limiter is not None:
            try:
                decision = await _resolve_rate_limit(rate_limiter.check(http_request))
            except Exception as exc:  # noqa: BLE001 — stable integration failure contract
                telemetry.increment("http_rate_limit_failures_total", {"route": "/v1/invoke"})
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "rate_limiter_unavailable",
                        "message": "Rate limiter unavailable",
                    },
                ) from exc
            if not decision.allowed:
                telemetry.increment("http_rate_limit_rejections_total", {"route": "/v1/invoke"})
                headers = {"Retry-After": str(decision.retry_after_seconds)}
                if decision.limit is not None:
                    headers["X-RateLimit-Limit"] = str(decision.limit)
                if decision.remaining is not None:
                    headers["X-RateLimit-Remaining"] = str(decision.remaining)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    headers=headers,
                    detail={
                        "code": "rate_limited",
                        "message": "Rate limit exceeded",
                        "retry_after_seconds": decision.retry_after_seconds,
                    },
                )
        agent_request = AgentRequest(
            input=request.input,
            session_id=request.session_id,
            caller_metadata=request.caller_metadata,
            timeout_seconds=request.timeout_seconds,
            caller_identity=identity.caller if identity else None,
            user_context=identity.user if identity else None,
            continuation_id=request.continuation_id,
            approval_decision=request.approval_decision,
            checkpoint_id=request.checkpoint_id,
        )
        if request.request_id:
            agent_request.request_id = request.request_id
        telemetry.logger.info(
            "invoke request",
            request_id=agent_request.request_id,
            session_id=request.session_id,
            authenticated=identity is not None,
        )
        if _accepts_streaming(http_request):
            return StreamingResponse(
                _stream_invoke(agent, agent_request, telemetry),
                media_type=STREAMING_MEDIA_TYPE,
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        try:
            response = await agent.invoke(agent_request)
        except InvocationOverloadedError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": "1"},
                detail={"code": "invocation_overloaded", "limit": exc.limit},
            ) from exc
        except ContinuationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "continuation_not_found", "message": str(exc)},
            ) from exc
        except CheckpointNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "checkpoint_not_found", "message": str(exc)},
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

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Expose the in-memory operational series for a Prometheus scraper."""
        exporter = getattr(telemetry.metrics, "prometheus_text", None)
        content = exporter() if callable(exporter) else ""
        return Response(content=content, media_type="text/plain; version=0.0.4")

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

    @app.get("/openapi.json", include_in_schema=False)
    async def legacy_openapi() -> JSONResponse:
        """Keep the pre-versioned OpenAPI URL as a compatibility alias."""
        return JSONResponse(app.openapi())

    @app.middleware("http")
    async def propagate_trace_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Bridge W3C trace context at the HTTP boundary when OTel is enabled."""
        context_token = telemetry.attach_context(request.headers)
        span = telemetry.start_span(
            "http.request",
            attributes={
                "http.method": request.method,
                "http.route": request.url.path,
            },
        )
        started = time.monotonic()
        try:
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            telemetry.inject_context(response.headers)
            return response
        except Exception as exc:  # noqa: BLE001 — preserve the HTTP error contract
            span.add_event("http.error", {"error.type": type(exc).__name__})
            raise
        finally:
            telemetry.record(
                "http_request_latency_ms",
                round((time.monotonic() - started) * 1000, 2),
                {"route": request.url.path, "method": request.method},
            )
            telemetry.finish_span(span)
            telemetry.detach_context(context_token)

    return app
