"""FastAPI application factory (local adapter, medrixa.http.v1).

Security defaults: authentication on every route except /v1/health, host
allow-list, bounded and strictly parsed bodies, uniform error envelope with a
request id, no-store/nosniff/CSP headers, OpenAPI UI only in ``development``.
"""
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Path, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.contracts import InputRejected, parse_strict
from app.persistence.memory import InMemoryStateRepository
from app.persistence.repository import StateRepository
from app.observability.context import request_id_var
from app.observability.metrics import InMemoryMetrics
from app.security.ratelimit import RateLimiter, TokenBucketLimiter
from app.web.policy import Forbidden, OutOfScope, require
from app.web import API_VERSION
from app.web.audit import AuditSink, LoggingAuditSink
from app.web.auth import AuthenticationFailed, Authenticator, HmacTokenAuthenticator, Principal
from app.web.contracts import (CreateRunRequest, ErrorCode, ErrorResponse, ReadyView, HealthView, RecoveryView, RunEventsView,
                               RunView)
from app.web.errors import ApiError, error_payload
from app.web.service import IDEMPOTENCY_KEY, RunService
from app.web.settings import Settings

_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_DOC_PATHS = {"/docs", "/docs/oauth2-redirect", "/openapi.json"}
_STATUS_CODES: dict[int, ErrorCode] = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE",
                 415: "UNSUPPORTED_MEDIA_TYPE", 401: "UNAUTHENTICATED", 403: "FORBIDDEN"}


def _errors(*statuses: int) -> dict:
    return {status: {"model": ErrorResponse} for status in statuses}


def _json(model, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status, headers=headers)


def create_app(settings: Settings, repository: StateRepository | None = None,
               audit: AuditSink | None = None, authenticator: Authenticator | None = None,
               runner_factory=None, leases=None, metrics: InMemoryMetrics | None = None,
               execution=None) -> FastAPI:
    settings = Settings.model_validate(settings)
    if repository is None:
        if settings.postgres_dsn is not None:
            from app.persistence.postgres import PostgresRunLeases, PostgresStateRepository
            dsn = settings.postgres_dsn.get_secret_value()
            repository = PostgresStateRepository(dsn, migrate=settings.postgres_migrate,
                                                 pool_size=settings.postgres_pool_size)
            leases = leases or PostgresRunLeases(dsn, migrate=settings.postgres_migrate, repository=repository)
        elif settings.store_dir is not None:
            from app.persistence.leases import LocalFileRunLeases
            from app.persistence.local_file import ProvisionalLocalFileRepository
            repository = ProvisionalLocalFileRepository(settings.store_dir)
            leases = leases or LocalFileRunLeases(settings.store_dir / "leases")
        else:
            repository = InMemoryStateRepository()  # explicit: nothing survives a restart
    if leases is None:
        from app.persistence.leases import InMemoryRunLeases
        leases = InMemoryRunLeases()  # same-process liveness only
    if authenticator is None:
        if settings.auth_mode == "oidc" and settings.oidc is not None:
            from app.web.oidc import OidcJwtAuthenticator
            authenticator = OidcJwtAuthenticator(settings.oidc)
        elif settings.auth_secret is not None:
            authenticator = HmacTokenAuthenticator(settings.auth_secret.get_secret_value(),
                                                   settings.token_max_ttl_seconds, settings.clock_skew_seconds)
        else:  # unreachable: Settings validation guarantees one mode is configured
            raise ValueError("no authentication configured")
    active_authenticator: Authenticator = authenticator
    service_args = {"runner_factory": runner_factory} if runner_factory else {}
    registry = metrics if metrics is not None else InMemoryMetrics()  # P8
    service = RunService(repository, audit or LoggingAuditSink(), leases=leases,
                         lease_ttl_seconds=settings.lease_ttl_seconds, max_active_runs=settings.max_active_runs,
                         metrics=registry, execution=execution, **service_args)
    registry.add_collector(lambda m: m.set_gauge("medrixa_active_runs", service.active_runs))
    pool = getattr(repository, "_pool", None)
    if pool is not None:
        def pool_gauges(m: InMemoryMetrics) -> None:
            current = getattr(repository, "_pool", None)  # None once closed
            if current is None:
                return
            stats = current.get_stats()
            m.set_gauge("medrixa_db_pool_connections", stats.get("pool_size", 0), {"state": "open"})
            m.set_gauge("medrixa_db_pool_connections", stats.get("pool_available", 0), {"state": "idle"})
            m.set_gauge("medrixa_db_pool_connections", stats.get("requests_waiting", 0), {"state": "waiting"})
        registry.add_collector(pool_gauges)
    ip_limiter: RateLimiter = TokenBucketLimiter(settings.rate_limit_ip_per_minute)
    subject_limiter: RateLimiter = TokenBucketLimiter(settings.rate_limit_subject_per_minute)
    allowed_hosts = frozenset(settings.allowed_hosts)

    owned_repository = repository

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            close = getattr(owned_repository, "close", None)
            if callable(close):
                close()  # P7.6: release pooled database connections on shutdown

    app = FastAPI(title="MEDRIXA AI — local API (provisional)", version=API_VERSION, lifespan=lifespan,
                  description="Local synthetic-data simulator. Not a medical device; no clinical validation. "
                              "All results require human review.",
                  docs_url="/docs" if settings.expose_docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if settings.expose_docs else None)
    app.state.service = service
    app.state.settings = settings

    def request_id_of(request: Request) -> str:
        return getattr(request.state, "request_id", None) or uuid.uuid4().hex

    def error_response(request: Request, code: ErrorCode, retry_after: int | None = None) -> JSONResponse:
        status, body = error_payload(code, request_id_of(request))
        headers = {"WWW-Authenticate": 'Bearer realm="medrixa"'} if status == 401 else {}
        if retry_after is not None:
            headers["Retry-After"] = str(int(retry_after))
        return JSONResponse(body, status_code=status, headers=headers or None)

    @app.middleware("http")
    async def security_envelope(request: Request, call_next):
        started = time.perf_counter()
        supplied = request.headers.get("x-request-id", "")
        request.state.request_id = supplied if _REQUEST_ID.match(supplied) else uuid.uuid4().hex
        context_token = request_id_var.set(request.state.request_id)  # P8.3: log correlation
        try:
            host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
            retry = None
            if host in allowed_hosts:
                # Before authentication: also bounds credential guessing per client address.
                retry = ip_limiter.check(request.client.host if request.client else "unknown")
            if host not in allowed_hosts:
                response = error_response(request, "INVALID_INPUT")
            elif retry is not None:
                registry.increment("medrixa_rate_limited_total", {"scope": "address"})
                response = error_response(request, "RATE_LIMITED", int(retry))
            else:
                try:
                    response = await call_next(request)
                except Exception:  # last resort: never leak a stack trace or message
                    response = error_response(request, "INTERNAL_ERROR")
            for name, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)
            if request.url.path not in _DOC_PATHS:
                response.headers["Content-Security-Policy"] = _API_CSP
            response.headers["X-Request-ID"] = request.state.request_id
            # P8: bounded labels only (route template, never the raw path or identifiers).
            route = getattr(request.scope.get("route"), "path", None) or "unrouted"
            method = request.method if request.method in _METHODS else "OTHER"
            registry.increment("medrixa_http_requests_total",
                               {"method": method, "route": route, "status": str(response.status_code)})
            registry.observe("medrixa_http_request_duration_seconds", time.perf_counter() - started,
                             {"method": method, "route": route})
            return response
        finally:
            request_id_var.reset(context_token)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return error_response(request, exc.code, exc.retry_after)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request, "INVALID_INPUT")  # FastAPI's default would echo the input

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return error_response(request, _STATUS_CODES.get(exc.status_code, "INVALID_INPUT"))

    async def authenticate(request: Request) -> Principal:
        try:
            # In a worker thread: OIDC key refresh performs bounded network I/O.
            principal = await run_in_threadpool(active_authenticator.authenticate,
                                                request.headers.get("authorization"))
        except AuthenticationFailed:
            registry.increment("medrixa_auth_failures_total")
            service.audit_auth_failure(request.state.request_id)
            raise ApiError("UNAUTHENTICATED") from None
        retry = subject_limiter.check(f"{principal.organization or '-'}\x00{principal.subject}")
        if retry is not None:
            registry.increment("medrixa_rate_limited_total", {"scope": "identity"})
            raise ApiError("RATE_LIMITED", int(retry))
        return principal

    async def read_body(request: Request) -> bytes:
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            raise ApiError("UNSUPPORTED_MEDIA_TYPE")
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > settings.max_body_bytes):
            raise ApiError("PAYLOAD_TOO_LARGE")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > settings.max_body_bytes:
                raise ApiError("PAYLOAD_TOO_LARGE")
        return bytes(body)

    bearer: dict[str, Any] = {"security": [{"bearerAuth": []}]}

    @app.get("/v1/health", response_model=HealthView, tags=["system"], responses=_errors(429))
    async def health() -> JSONResponse:
        return _json(HealthView())

    def readiness() -> bool:
        try:
            repository.ping()
            return bool(active_authenticator.ready() and service.audit_ready())
        except Exception:  # any failure means "not ready"; details stay out of the response
            return False

    @app.get("/v1/ready", response_model=ReadyView, tags=["system"], responses=_errors(429, 503),
             summary="Readiness: store reachable, schema migrated, token keys available, audit configured")
    async def ready() -> JSONResponse:
        if await run_in_threadpool(readiness):
            return _json(ReadyView())
        raise ApiError("NOT_READY", retry_after=5)

    @app.get("/v1/metrics", tags=["system"], responses=_errors(401, 403, 429), openapi_extra=bearer,
             summary="Prometheus text metrics (aggregate counters only; no identifiers)",
             response_class=PlainTextResponse)
    async def metrics_endpoint(request: Request) -> PlainTextResponse:
        principal = await authenticate(request)
        try:
            require(principal, "metrics:read")
        except (Forbidden, OutOfScope):
            raise ApiError("FORBIDDEN") from None
        return PlainTextResponse(registry.render_prometheus(), media_type=PROMETHEUS_CONTENT_TYPE)

    @app.post("/v1/runs", status_code=201, response_model=RunView, tags=["runs"],
              summary="Create and execute a synthetic run (idempotent)",
              responses=_errors(400, 401, 403, 409, 413, 415, 422, 429, 500, 503),
              openapi_extra={**bearer, "parameters": [{
                  "name": "Idempotency-Key", "in": "header", "required": True,
                  "description": "Client-generated key, unique per intended run (e.g. a UUID). "
                                 "Retries with the same key and body replay the original result.",
                  "schema": {"type": "string", "pattern": IDEMPOTENCY_KEY.pattern}}],
                  "requestBody": {"required": True, "content": {"application/json": {
                      "schema": {"$ref": "#/components/schemas/CreateRunRequest"}}}}})
    async def create_run(request: Request) -> JSONResponse:
        principal = await authenticate(request)
        idempotency_key = request.headers.get("idempotency-key", "")
        if not IDEMPOTENCY_KEY.match(idempotency_key):
            raise ApiError("IDEMPOTENCY_KEY_REQUIRED")
        payload = await read_body(request)
        try:
            body = parse_strict(payload, CreateRunRequest, settings.max_body_bytes)
        except InputRejected:
            raise ApiError("INVALID_INPUT") from None
        view, replayed = await service.create_run(principal, body.case, idempotency_key, request.state.request_id)
        return _json(view, 201, {"Location": f"/v1/runs/{view.run_id}",
                                 "Idempotent-Replayed": "true" if replayed else "false"})

    run_id_param = Annotated[str, Path(description="Run identifier (opaque).")]

    @app.get("/v1/runs/{run_id}", response_model=RunView, tags=["runs"],
             responses=_errors(401, 403, 404, 429, 500, 503), openapi_extra=bearer)
    async def get_run(request: Request, run_id: run_id_param) -> JSONResponse:
        principal = await authenticate(request)
        return _json(await run_in_threadpool(service.get_run, principal, run_id, request.state.request_id))

    @app.get("/v1/runs/{run_id}/events", response_model=RunEventsView, tags=["runs"],
             responses=_errors(401, 403, 404, 429, 500, 503), openapi_extra=bearer)
    async def get_events(request: Request, run_id: run_id_param) -> JSONResponse:
        principal = await authenticate(request)
        return _json(await run_in_threadpool(service.get_events, principal, run_id, request.state.request_id))

    @app.post("/v1/admin/recovery", response_model=RecoveryView, tags=["operations"],
              summary="Move interrupted runs to human review (never re-executes agents)",
              responses=_errors(401, 403, 409, 429, 500, 503), openapi_extra=bearer)
    async def recover(request: Request) -> JSONResponse:
        principal = await authenticate(request)
        view = await run_in_threadpool(service.recover, principal, request.state.request_id)
        return JSONResponse(view.public())

    def openapi_schema() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        components = schema.setdefault("components", {})
        request_schema = CreateRunRequest.model_json_schema(ref_template="#/components/schemas/{model}")
        components.setdefault("schemas", {}).update(request_schema.pop("$defs", {}))
        components["schemas"]["CreateRunRequest"] = request_schema
        # FastAPI documents its default 422 validation body, which this API never
        # emits (validation errors use the uniform 400 envelope): remove it.
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                default_422 = operation.get("responses", {}).get("422", {})
                if "HTTPValidationError" in json.dumps(default_422):
                    del operation["responses"]["422"]
        for name in ("HTTPValidationError", "ValidationError"):
            components["schemas"].pop(name, None)
        components["securitySchemes"] = {"bearerAuth": {
            "type": "http", "scheme": "bearer",
            "description": "Bearer token: OIDC access token (JWT, MEDRIXA_AUTH_MODE=oidc) or the "
                           "provisional local HMAC token (mdx1, development/test)."}}
        app.openapi_schema = jsonable_encoder(schema)
        return app.openapi_schema

    app.openapi = openapi_schema  # type: ignore[method-assign]  # FastAPI's documented customisation hook
    return app
