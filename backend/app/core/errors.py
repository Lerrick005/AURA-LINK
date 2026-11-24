"""Application error hierarchy and the single error response shape.

Every error the API returns goes through one of these classes or the
handlers in main.py. The wire format is:

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": {...}}}

`request_id` is the same value that appears in the X-Request-ID response
header, so a client can correlate an error report with server logs.

Rules:
- Do NOT raise HTTPException directly in new code. Use these classes.
- `details` must not contain secrets or raw exception messages.
- The 500 handler logs the full exception; the client sees only `code`.
"""
from __future__ import annotations
from typing import Any


class AppError(Exception):
    """Base for all typed application errors.

    `status_code` is the HTTP status. `code` is a stable machine-readable
    string clients may branch on (never change once shipped). `message`
    is human-readable and may change. `details` is structured context.
    """
    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ):
        self.message = message or self.code.replace("_", " ")
        self.details = details
        super().__init__(self.message)


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class InternalError(AppError):
    status_code = 500
    code = "internal_error"
