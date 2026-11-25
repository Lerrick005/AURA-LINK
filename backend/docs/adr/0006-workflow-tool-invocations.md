# ADR-0006: Workflow Tool Invocations Bypass `tool_calls`

Status: Accepted
Date: 2026-11-24

## Context
The agent engine (Stage 2) writes every tool invocation to `tool_calls` for
idempotency, approval tracking, and cross-run analytics. The workflow engine
(Stage 3.1e) invokes tools directly and records the result only in
`execution_steps`. This means workflow tool runs are invisible to any query
that joins against `tool_calls`.

## Decision
Workflow tool nodes do not write to `tool_calls`. `execution_steps`
(type='tool') is the single source of truth for workflow tool execution.
Idempotency is provided by a deterministic key `wf:{run_id}:{node.id}` and
stable across crash-resume of the same run.

## Alternatives Considered
- Write to `tool_calls` from workflow too. Rejected for MVP because tool_calls
  has FK constraints to `agent_version_id` and `step_id` that do not fit the
  workflow run shape (nullable agent_version_id, steps that are not proposals).
  Would require either a schema split or dual-purpose columns.

## Consequences
- Negative: any analytics joining `tool_calls` will miss workflow tool runs.
  Workflow analytics must source from `execution_steps`.
- Negative: high-risk tool approval gates (Stage 3.5) require extending the
  workflow path independently of the agent path.
- Positive: `tool_calls` stays clean — one shape, one purpose.
- Positive: workflow tool execution is fully visible in `GET /runs/{id}` with
  node_id, tool_key, arguments, output, error.

## Revisit When
- Stage 3.5 introduces manual approval for high-risk workflow tools.
- A customer requires unified tool-execution analytics across agents and
  workflows.
