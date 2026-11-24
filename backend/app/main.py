"""Auralink FastAPI application.

Wires logging, request-ID middleware, CORS, and the v1 router.
Call site for uvicorn:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.health import router as health_router
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from app.core.errors import AppError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.modules.auth.router import router as auth_router
from app.modules.runs.routes import router as runs_router
from app.modules.workflows.routes import router as workflows_router
from app.core import logging as alog
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.modules.tools.echo import register_echo_tool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: configure logging. Shutdown: dispose DB engine."""
    alog.configure_logging()
    log = alog.get_logger("app.startup")
    s = get_settings()
    log.info("app_started", extra={"env": s.APP_ENV, "version": s.APP_VERSION})
    register_echo_tool()
    log.info("tool_registry_initialized", extra={"tools": ["echo"]})
    try:
        yield
    finally:
        await dispose_engine()
        log.info("app_stopped")


def _is_safe_id(value: str | None) -> bool:
    """Accept caller-supplied request IDs only if they look like hex/base36 tokens."""
    if not value or len(value) > 128:
        return False
    return all(c.isalnum() or c in "-_" for c in value)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Auralink",
        version=s.APP_VERSION,
        lifespan=lifespan,
        docs_url=None if s.is_production else "/docs",
        redoc_url=None if s.is_production else "/redoc",
        openapi_url=None if s.is_production else "/openapi.json",
    )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=s.trusted_hosts_list)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Trace-ID"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming if _is_safe_id(incoming) else uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        alog.bind_context(request_id=request_id, trace_id=trace_id)
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception as exc:
            alog.log_exception(alog.get_logger("app.request"), exc, ctx={"path": request.url.path})
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            alog.get_logger("app.request").info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code if response is not None else 500,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            alog.clear_context()
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        return response

    def _error_body(code: str, message: str, request_id: str, details=None):
        body = {"error": {"code": code, "message": message, "request_id": request_id}}
        if details is not None:
            body["error"]["details"] = details
        return body

    def _error_headers(request_id: str, trace_id: str = "") -> dict:
        h = {"X-Request-ID": request_id}
        if trace_id:
            h["X-Trace-ID"] = trace_id
        return h

    def _rid_and_tid(request: Request) -> tuple[str, str]:
        rid = getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")
        tid = getattr(request.state, "trace_id", "")
        return rid, tid

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id, trace_id = _rid_and_tid(request)
        if exc.status_code >= 500:
            alog.log_exception(alog.get_logger("app.error"), exc, ctx={"path": request.url.path})
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, request_id, exc.details),
            headers=_error_headers(request_id, trace_id),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id, trace_id = _rid_and_tid(request)
        code = "http_" + str(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, message, request_id),
            headers=_error_headers(request_id, trace_id),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id, trace_id = _rid_and_tid(request)
        details = {"errors": exc.errors()}
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", "Request validation failed", request_id, details),
            headers=_error_headers(request_id, trace_id),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id, trace_id = _rid_and_tid(request)
        alog.log_exception(alog.get_logger("app.unhandled"), exc, ctx={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "An internal error occurred", request_id),
            headers=_error_headers(request_id, trace_id),
        )


    app.include_router(health_router, prefix=s.API_V1_PREFIX)
    app.include_router(auth_router, prefix=s.API_V1_PREFIX)
    app.include_router(workflows_router, prefix=s.API_V1_PREFIX)
    app.include_router(runs_router, prefix=s.API_V1_PREFIX)
    return app


app = create_app()






