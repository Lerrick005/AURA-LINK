# ADR-0007: RunPrimitives Extraction Deferral

Status: Accepted
Date: 2026-11-24

## Context
`WorkflowEngine` inherits from `RuntimeEngine` to reuse lease, fencing, and step-writing logic. However, as seen in 3.1d and 3.1e, `WorkflowEngine` is forced to override methods like `_redact_step_output` and add parallel methods like `_resolve_tool_by_key` because the workflow execution context differs from the agent execution context.

## Decision
We will defer the extraction of a `RunPrimitives` composition object (to replace inheritance) until Stage 3.2. 
For now, inheritance remains the pragmatic choice for MVP. The `WorkflowEngine` cleanly overrides the state loader and specific dispatch/redaction methods without touching the core lease mechanics.

## Consequences
- Negative: Technical debt. `WorkflowEngine` is coupled to `RuntimeEngine`'s internal method signatures (`_fence_and_heartbeat`, `_append_step`).
- Positive: We shipped Stage 3.1 significantly faster.
- Positive: We now have concrete evidence of *what* needs to be extracted (redaction overrides, tool resolution, terminal writes).

## Revisit When
- Stage 3.2 (API layer) is complete.
- We need to introduce a third execution engine (e.g., a background batch processor).
