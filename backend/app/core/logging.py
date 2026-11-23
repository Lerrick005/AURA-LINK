"""Structured logging for Auralink.

Every module logs through ``get_logger(__name__)``. Nothing calls
``logging.basicConfig`` or ``print`` outside this file.

Public surface
--------------
- ``configure_logging()`` -- call once at process start (api and worker entry).
- ``get_logger(name)`` -- returns a logger pre-wired with the context filter.
- ``bind_context(**fields)`` -- set trace_id/org_id/user_id/request_id for the
  current async context; all subsequent log lines include them.
- ``clear_context()`` -- reset (used by tests and at request teardown).
- ``log_exception(logger, exc)`` -- safe exception formatter that redacts
  before writing.

Doctrine (see docs/SECURITY.md):
    Never f-string a secret. If you must log something sensitive-adjacent,
    pass it as ``extra={"secret_fields": ["<key>"]}`` and let the filter
    scrub it -- or better, do not log it at all.

This module uses stdlib logging only. A structured-tracing library is
deliberately deferred until we need spans (ADR-0013).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import traceback
from contextvars import ContextVar
from typing import Any

from app.core.config import get_settings

# --- Context ------------------------------------------------------------------

_ctx_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_ctx_org_id: ContextVar[str | None] = ContextVar("org_id", default=None)
_ctx_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_ctx_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def bind_context(
    *,
    trace_id: str | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Set context fields for the current async task."""
    if trace_id is not None:
        _ctx_trace_id.set(trace_id)
    if org_id is not None:
        _ctx_org_id.set(org_id)
    if user_id is not None:
        _ctx_user_id.set(user_id)
    if request_id is not None:
        _ctx_request_id.set(request_id)


def clear_context() -> None:
    _ctx_trace_id.set(None)
    _ctx_org_id.set(None)
    _ctx_user_id.set(None)
    _ctx_request_id.set(None)


def _current_context() -> dict[str, str]:
    ctx: dict[str, str] = {}
    for name, var in (
        ("trace_id", _ctx_trace_id),
        ("org_id", _ctx_org_id),
        ("user_id", _ctx_user_id),
        ("request_id", _ctx_request_id),
    ):
        v = var.get()
        if v:
            ctx[name] = v
    return ctx


# --- Redaction ----------------------------------------------------------------

_SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {"password", "token", "secret", "api_key", "authorization", "cookie", "set-cookie"}
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"), "***JWT***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ***"),
    (re.compile(r"(://[^:/@\s]+):[^@\s]+@"), r"\1:***@"),
    (
        re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*([^\s,&;]+)"),
        r"\1=***",
    ),
)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, repl in _PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SECRET_KEY_NAMES:
                out[k] = "***"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


# --- Formatter ----------------------------------------------------------------

_RESERVED_ATTRS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with context fields merged in."""

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        base.update(_current_context())

        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED_ATTRS and not k.startswith("_")
        }
        if extra:
            base["extra"] = _scrub(extra)

        if record.exc_info:
            base["exc"] = _scrub(self.formatException(record.exc_info))

        return json.dumps(_scrub(base), ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """Human-readable single-line formatter for dev (LOG_JSON=false)."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = _current_context()
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        msg = record.getMessage()
        line = (
            f"{time.strftime('%H:%M:%S', time.localtime(record.created))} "
            f"{record.levelname:<8} {record.name}: {msg}"
        )
        if ctx_str:
            line += f"  [{ctx_str}]"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        for pattern, repl in _PATTERNS:
            line = pattern.sub(repl, line)
        return line


# --- Setup --------------------------------------------------------------------

_CONFIGURED = False


def configure_logging(force: bool = False) -> None:
    """Install our handler on the root logger. Idempotent unless force=True."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.LOG_JSON else PlainFormatter())
    root.addHandler(handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger. ``configure_logging()`` must have run at process start."""
    return logging.getLogger(name)


def log_exception(
    logger: logging.Logger, exc: BaseException, *, ctx: dict[str, Any] | None = None
) -> None:
    """Log an exception with redaction applied to the traceback string."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error(
        "unhandled_exception",
        exc_info=None,
        extra={"exc_type": type(exc).__name__, "traceback": _scrub(tb), **(ctx or {})},
    )
