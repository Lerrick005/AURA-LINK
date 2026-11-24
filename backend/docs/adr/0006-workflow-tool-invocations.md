# ADR-0006: Workflow Tool Invocations bypass `tool_calls` table

Status: Accepted
Date: 2026-11-24

## Context
In Stage 2 (Agent Engine), every tool invocation writes a row to the `tool_calls` table, managing idempotency, approvals, and side-effect tracking. 
In Stage 3.1e (Workflow Engine), we added `_step_tool`, which invokes tools directly via the `ToolExecutor` protocol but *does not* write to `tool_calls`. It only records the result in `execution_steps`.

## Decision
For MVP, workflow tool nodes will not write to `tool_calls`. The `execution_steps` table (type: 'tool') is the single source of truth for workflow tool execution.
- We use a deterministic idempotency key `wf:{run_id}:{node.id}` to prevent duplicate side effects on crash-resume.
- We bypass the `tool_calls` table because workflow tools are explicitly authored by the workflow designer, not dynamically proposed by an LLM. The approval gate and policy check mechanisms built for agents are currently unnecessary for static workflow tools.

## Consequences
- Negative: Analytics that join `tool_calls` will miss workflow tool executions. We must use `execution_steps` for workflow analytics.
- Negative: If a workflow tool is classified as high-risk, we currently execute it without the agentic approval flow.
- Positive: Avoids polluting `tool_calls` with rows that don't have an `agent_version_id` or `step_id` in the same shape as agent tools.

## Revisit When
- Stage 3.5 introduces manual approval gates for high-risk workflow tools.
- Analytics requirements demand a unified view of all tool executions across agents and workflows.
