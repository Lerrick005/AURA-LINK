# ADR-0002: Tool registration consistency

---

Status: Accepted
Date: 2026-11-24
Deciders: Engineering

## Context

A tool in Auralink has two registrations:

1. A row in tools - schema, risk class, approval policy, idempotency
   mode, timeout. Governs grant visibility and policy.
2. A Python executor registered via register_*_tool() at startup.
   Governs what happens when called.

Nothing enforces that both exist. During Stage 2 debugging:

- Migration 0001_initial seeded DBrows for channel.reply,
  handoff.request, knowledge.search. No executors exist for any.
- register_echo_tool() registers an executor for echo. No DB row
  exists for it.
- The smoke test worked only because it manually inserted the missing
  echo row.

## Decision

Enforce consistency at three checkpoints.

### Checkpoint 1 - Startup validation

In app/main.py lifespan startup, after all register_*_tool() calls,
walk the tools table and the registry. Compute two sets: DB keys not in
registry, and registry keys not in DB. If either set is non-empty, raise
an error listing the orphans. In production (APP_ENV == "prod"), refuse to
start. In development, log a warning and continue.

### Checkpoint 2 - Grant-time validation

grant_tools requires an executor to exist for the tool key being
granted. You cannot grant a tool the runtime cannot execute.

### Checkpoint 3 - Execution-time safety net

_step_tool_call already escalates when executor_for() raises. Keep
this as the last line of defense. It should never fire if checkpoints
1 and 2 are in place. If it does, log it as a critical invariant
violation.

## Alternatives considered

/ Single-source-of-truth DB with reference-counted executors: too
  complex, requires registry-of-registries.
- Decorator-based registration that also writes DB row: wrong,
  because schema and risk class are policy decisions that belong in
  migrations, not code annotations.
- No validation, rely on tests: failed. Tests cannot enumerate tools
  cross registry because the registry is process-local.

## Consequences

Positive:
- Half-registered tools fail at startup (prod) or grant time (always),
  never at execution time.
- Adding a tool is an explicit checklist: migration + executor +
  register call.
- Error message names the tool and what is missing.

Negative:
- Requires a startup DB connection (already happens for other checks).
- Dev-warning vs prod-fail split adds one conditional.

Risks:
- A tool that is intentionally DBB-only (planned, executor coming later)
  will block the startup check. Resolution: such tools marked
  status=planned and excluded. Stage 4+ concern.

## Revisit when

- Per-org custom tools are added in Stage 4: the check becomes per-org
  or exempts custom tools.
- The tool registry is extracted to a service: the check becomes an
  inter-service health check.
