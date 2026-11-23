"""The single chokepoint for redacting customer data before persistence.

Doctrine (see SECURITY.md):
    Everything the runtime persists -- tool inputs, tool outputs, errors,
    tracebacks, structured exception metadata -- goes through redact().
    Nothing else may write tool payloads to the DB.

Policy:
    - Allowlist keys at any depth survive with their scalar value IF the
      scalar is a short primitive (str <= 64 chars, int, float, bool, None).
    - Anything else is replaced with a marker string.
    - Depth is unbounded to 32; deeper structures are replaced with a fixed
      marker to prevent stack exhaustion.
    - Errors are redacted the same as payloads.
"""

from __future__ import annotations

from typing import Any


_ALLOWLIST_KEYS: frozenset[str] = frozenset(
    {
        "message_id",
        "delivery_status",
        "escalation_id",
        "count",
        "latency_ms",
        "status",
        "error_type",
        "token_count",
        "tool_key",
        "risk_class",
        "verified",
        "id",
        "kind",
    }
)

_MAX_ALLOWLISTED_STR = 64


def _redact_primitive(v: Any, *, allowed: bool) -> Any:
    """Redact a non-container value according to the allowlist flag."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        if allowed and len(v) <= _MAX_ALLOWLISTED_STR:
            return v
        return f"<redacted:str:len={len(v)}>"
    return "<redacted>"


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a redacted copy of value, safe to persist or log."""
    if _depth > 32:
        return "<redacted:deep>"

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            key_str = k if isinstance(k, str) else str(k)
            if isinstance(v, (dict, list, tuple)):
                out[key_str] = redact(v, _depth=_depth + 1)
            else:
                out[key_str] = _redact_primitive(v, allowed=(key_str in _ALLOWLIST_KEYS))
        return out

    if isinstance(value, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in value]

    return _redact_primitive(value, allowed=False)


def redact_error(exc: BaseException) -> dict[str, Any]:
    """Structure a BaseException for storage with all fields redacted."""
    return {
        "error_type": type(exc).__name__,
        "message": redact(str(exc)),
        "args": redact(list(exc.args)),
    }
