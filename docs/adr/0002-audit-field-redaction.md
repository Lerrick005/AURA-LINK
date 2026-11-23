# ADR-0002: Audit fields bypass the redaction chokepoint

Status: Accepted
Date: 2026-11-23

## Context

`redact()` is the single chokepoint for customer data before persistence
(see SECURITY.md). It allowed strings through only if the key was in
`_ALLOWLIST_KEYS` and the value was <= 64 chars. The allowlist omitted several
fields that the runtime engine writes to `execution_steps.input`,
`escalations.context`, and `tool_calls.error`: `reason`, `tool`,
`last_step_type`, `terminal_tool_status`, `idempotency_mode`,
`approval_policy`, `step_type`, `expected`, `satisfied`, `limit`, `from`.

Consequence: escalation records existed but were unreadable. An escalation
row's `context.last_step_type` was `<redacted:str:len=13>` instead of
`"llm_response"`; `execution_steps.input->>'reason'` was
`<redacted:str:len=11>` instead of `"not_granted"`. This defeats the purpose
of §4.1-G and §4.1-I: a human reviewer cannot resolve an escalation they
cannot read.

## Decision

Extend `_ALLOWLIST_KEYS` to cover the audit fields above. They are internal
enum strings, tool identifiers, and reason codes -- never PII, credentials,
or customer payloads. Payload fields (`input`, `output`, `arguments`,
`content`, `message`, `args`) remain redacted as before.

## Trade-off

Tool keys are now readable in the DB. A tool key like
`salesforce.update_contact` is visible. This is acceptable because:

1. Tool keys are developer-facing identifiers, not customer data.
2. The arguments passed to the tool (`arguments`, `input`) remain redacted.

Mitigation (TODO, tracked): enforce a key pattern
`^[a-z][a-z0-9_.]{0,99}$` at tool registration, documented in SECURITY.md.
This prevents a future tool from having a PII-laden key.

## Revisit when

- Any new audit field is added to `execution_steps.input`,
  `escalations.context`, or `tool_calls.error`. The allowlist must be extended
  in the same PR that introduces the field, or the new field will be unreadable.
