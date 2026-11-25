# ADR-0008: Error Envelope Drops HTTPException.detail

Status: Accepted
Date: 2026-11-24

## Context
Stage 1 installed a global exception handler that wraps every HTTPException
into a standard envelope:

    {"error": {"code": "http_422", "message": "HTTP error", "request_id": "..."}}

This intentionally drops `HTTPException.detail` because Stage 1 could not
guarantee that detail content was safe to surface (business data, internal
state). The trade-off: clients cannot distinguish "invalid_workflow_graph"
from any other 422 without parsing logs.

## Decision
For now, keep the envelope as-is. Client-side error handling uses the HTTP
status + code (`http_422`, `http_404`, `http_401`) as the discriminating
signal. Detailed error messages are surfaced only in server logs.

## Alternatives Considered
- Include `detail` verbatim: rejected — the Stage 1 handler was hardened to
  prevent accidental data leaks; reversing that for one module is a security
  regression.
- Structured `detail` with a schema: correct long-term, but requires defining
  a `ValidationError` envelope and reworking every raise site across modules.
  This is a Stage 7 (production readiness) concern.

## Consequences
- Negative: the workflow 422 test cannot assert on validation errors — it
  asserts on the code only, and a comment points to this ADR.
- Negative: real client-side error UX (e.g. "Your graph has 3 errors: missing
  trigger, cycle, orphan node") is not possible until this ADR is superseded.
- Positive: the envelope is uniform across all endpoints from day one.

## Revisit When
- The frontend needs structured validation feedback (likely Stage 8).
- Any endpoint must surface Pydantic validation details to the user.
- Security review says the current envelope is over-conservative.
