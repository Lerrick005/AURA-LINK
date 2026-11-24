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
        # Pre-existing entries
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
        # Audit fields the runtime engine writes to execution_steps.input,
        # escalations.context, and tool_calls.error. These are internal enum
        # strings, tool identifiers, and reason codes -- never PII or
        # credentials. They must be readable by a human reviewer resolving an
        # escalation. See SECURITY.md and §4.1-G / §4.1-I of the blueprint.
        "reason",
        "tool",
        "last_step_type",
        "terminal_tool_status",
        "idempotency_mode",
        "approval_policy",
        "step_type",
        "expected",
        "satisfied",
        "limit",
        "from",
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


# --- audit meta redaction (added for audit/service.py) ---
_AUDIT_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "api_key", "apikey", "authorization", "cookie", "set-cookie",
    "private_key", "session_id", "bearer", "jwt", "credential",
})


def redact_audit_meta(value, *, _depth=0):
    """Redact known-sensitive KEYS in audit metadata, keep other values.

    Differs from redact(): redact() is fail-closed for all strings
    (used by engine.py execution data); redact_audit_meta is fail-open
    for non-sensitive keys because the audit trail must be operator-readable.
    Depth-capped at 32.
    """
    if _depth > 32:
        return "<truncated:depth>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            ks = str(k)
            if ks.lower() in _AUDIT_SENSITIVE_KEYS:
                out[ks] = "<redacted:sensitive>"
            else:
                out[ks] = redact_audit_meta(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_audit_meta(v, _depth=_depth + 1) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return "<redacted:bytes>"
    if isinstance(value, str):
        if len(value) > 512:
            return value[:512] + "<truncated>"
        return value
    return value
