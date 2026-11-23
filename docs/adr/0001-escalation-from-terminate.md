# ADR-0001: Escalation records are written from the terminate branch

Status: Accepted
Date: 2026-11-23

## Context

`decide_next_step` returns `StepDecision(kind="terminate", terminal_reason="escalated")`
on three paths: (1) `consecutive_rejections >= 3` (Precedence 2), (2) terminal
tool ran but verification did not confirm and `require_verified_terminal=True`
(Precedence 3), (3) terminal tool never ran after nudge was spent (Precedence 3).

Before this decision, `run_once`'s terminate branch called `_write_terminated`
without `_write_escalation`. Result: `agent_runs.terminal_reason` was
`"escalated"` but `escalations` was empty. This violated §4.1-G ("escalation
records with reason, context, agent assessment, prior actions, tool results,
suggested next step, priority, assignee, status, resolution") and §19
("execution pauses durably -> appears in Approvals inbox -> human
approves/rejects/edits").

## Decision

1. `StepDecision` carries an optional `escalation_reason: str | None`.
2. Each of the three `escalated` paths in `decide_next_step` sets a specific
   reason: `tool_rejections_exceeded`, `unverified_terminal_tool`, or
   `expected_tool_not_executed`.
3. `run_once`'s terminate branch, when `reason == "escalated"`, calls
   `_write_escalation` before `_write_terminated`, and threads the escalation
   id into the terminate step's detail.

## Alternatives considered

- Write escalations inside `decide_next_step`. Rejected: that function is pure
  (its own docstring: "pure, testable, no I/O"). Adding a DB write there breaks
  the contract and makes it untestable without a session.
- Write escalations in each `_write_terminated` caller. Rejected: five call
  sites; easy to miss one when a sixth is added. One chokepoint is safer.
- Extend `_write_terminated` with an `escalate: bool` parameter. Deferred: more
  invasive change to a widely-used helper; the current approach works with less
  surface area.

## Consequences

- Every `escalated` termination produces exactly one `escalations` row with a
  specific reason and a context snapshot (consecutive_rejections,
  last_step_type, terminal_tool_status, nudge_issued).
- Cost: one additional INSERT per escalated run. Negligible.
- Risk: none observed. Verified by `_smoke_escalation.py` from a fresh session.

## Revisit when

- A fourth `escalated` path is added to `decide_next_step`. The new path must
  set `escalation_reason`, or the fallback `"policy_terminate"` is used, which
  is less useful to a reviewer.
