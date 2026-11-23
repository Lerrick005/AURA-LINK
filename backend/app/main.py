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
from app.core import logging as alog
from app.core.config import get_settings
from app.core.db import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: configure logging. Shutdown: dispose DB engine."""
    alog.configure_logging()
    log = alog.get_logger("app.startup")
    s = get_settings()
    log.info("app_started", extra={"env": s.APP_ENV, "version": s.APP_VERSION})
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
    async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-request-id")
        request_id = incoming if _is_safe_id(incoming) else uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
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

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        alog.log_exception(alog.get_logger("app.unhandled"), exc, ctx={"path": request.url.path})
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    app.include_router(health_router, prefix=s.API_V1_PREFIX)
    return app


app = create_app()
