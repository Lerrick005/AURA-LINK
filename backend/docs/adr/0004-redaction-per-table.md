# ADR-0004: Redaction policy per table

---

Status: Accepted
Date: 2026-11-24
Deciders: Engineering

## Context

The original design had one redact() function, called everywhere.
Fail-closed: every string became <redacted:str:len=N>, every dict kept
keys but redacted values.

Stage 2 debugging found this wrong for execution_steps:

- Engine reads its own execution_steps.input/output back on subsequent
  steps.
- Fail-closed redaction replaced tool_id with a placeholder, failing
  SQL tookups.
- Replaced tool_calls[].name, breaking grant checks.

Engine couldn't read its own writes. Fundamental error:
execution_steps is durable engine state, not audit data.

Parallel discovery: audit_log had opposite problem. With redact(),
every audit row was unreadable. Audit log is supposed to be readable by
operators investigating incidents. Different redaction needed: fail-open
on non-sensitive keys.

Two tables, two requirements, one function. That's the bug.

## Decision

Each table with a redaction requirement declares its own policy at the
table level, not at call site.

### Table policies

**execution_steps** - structural redaction
- Preserves structural shape of every step payload (ADR-0001).
- Redacts only content field of text-carrying steps, plus fields
  declared sensitive by step type's model.
- Rationale: engine reads this table back. Breaking shape breaks engine.

**audit_log** - key-aware redaction (redact_audit_meta)
- Preserves all keys and non-sensitive values.
- Redacts values whose key is in _AUDIT_SENSITIVE_KEYS.
- Rationale: audit trail read by humans. Readability is the point.

**run_context** - structural redaction
- Same as execution_steps: engine state, must be readable.
- messages[].content and messages[].tool_calls[] preserved.

**tool_calls** - structural redaction
- Arguments preserved (needed for idempotency key derivation), result
  preserved (needed for verification).
- Sensitive values within arguments are executor's responsibility to
  redact before returning.

**llm_calls** (future) - fail-closed redaction
- Raw prompts/responses. Nothing reads them back for engine logic.
- Fail-closed: redact everything, keep byte length and key shape for
  forensic analysis.

### Enforcement

Meta-test asserts each table's persist path uses correct redaction
function matching declared policy.

## Alternatives considered

- One function with mode parameter. Call sites drift. Table-level
  declaration is stable.
- No redaction on engine state. Tempting: engine state is internal.
  But run_context.messages can contain PII. Redacting content while
  preserving structure is the right balance.
- Column-level GRANTS for sensitive data. Complementary, not
  alternative. Stage 7.

## Consequences

Positive:
- Engine reads its own state correctly.
- Audit log is readable.
- New tables force explicit policy decision.
- Policy documented once, not scattered.

Negative:
- Three redaction functions instead of one.
- Declared policy can drift from actual call site. Meta-test catches it.

Risks:
- Sensitive values in execution_steps not caught by structural
  redaction. Mitigation: per-step-type model (ADR-0001) marks which
  fields are content vs structural.
- New table added without policy. Meta-test fails on unknown tables.

## Revisit when

- Stage 4 knowledge engine adds strong privacy documents (medical,
  legal). Policy needs strict_fail_closed tier.
- Encryption at rest added - redaction policy and encryption policy
  need to compose.
