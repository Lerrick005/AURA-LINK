# ADR-0007: RunPrimitives Extraction Deferred to Stage 3.5

Status: Accepted
Date: 2026-11-24

## Context
`WorkflowEngine(RuntimeEngine)` inherits lease, fencing, heartbeat, step-write,
and terminal-state primitives. Over Stage 3.1 we accumulated three concrete
override sites that exist solely because WorkflowEngine is a subclass:

1. `_redact_step_output` — condition steps carry routing state that must not
   be redacted (chosen_next_node_id).
2. `_load_state` — returns `WorkflowState`, not `RunState`.
3. `_resolve_tool_by_key` — new helper, because RuntimeEngine only resolves by id.

These overrides are the evidence the ADR-004 revisit trigger asked for.

## Decision
Defer the extraction of a `RunPrimitives` composition object. Keep the
inheritance for now. Extract when the next engine (or the next permission-
context divergence) lands — realistically Stage 3.5 alongside approval gates.

## Alternatives Considered
- Extract now: cleaner architecture, but adds a refactor between shipped
  features and the next feature, with no user-visible benefit yet.
- Mixin: same coupling problem as inheritance.
- Composition: correct eventual shape, but too early without a third consumer.

## Consequences
- Negative: three override methods that would live in one place in a
  composition design.
- Positive: shipped Stage 3.1–3.3 faster with no user-facing bugs from the
  inheritance coupling.
- Positive: the override sites are documented (this ADR) so extraction is
  bounded work, not a discovery exercise.

## Revisit When
- Stage 3.5 (approval gates) needs workflow-specific pause semantics that
  do not map onto RuntimeEngine's verification/step model.
- A third execution engine (batch processor, long-running timer, etc.) is
  introduced.
- The override count exceeds 5.
