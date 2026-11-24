# ADR-0003: Step contract integration tests

---

Status: Accepted
Date: 2026-11-24
Deciders: Engineering

## Context

The runtime is a loop over steps. Each step reads its predecessors' state
and writes its own. The contract between adjacent steps is implicit.

Stage 2 debugging found one such violation:

- _step_policy_check wrote tool_proposed.input with tool_id,
  arguments, call_id, etc.
- _step_tool_call read proposed_input["tool_id"] - which existed in
  the writer but was redacted by the (now removed) blanket redactor.
- Mismatch wasn't caught by unit tests: each method tested in isolation.
- Caught only by smoke_e2e_.py, which wired the whole loop.

97 tests passed while the engine was broken. That's the gap.

## Decision

Require one integration test per adjacent step pair. Test:

1. Builds state up to step N (real DB, real tenant_session).
2. Calls step N with a realistic payload.
3. Asserts step N's output (or DB state after) satisfies step N+1's
   inputs.
4. Calls step O+1 and asserts it does not raise.

"Adjacent" means: step N produces state that step N+1 reads.

Current pairs:

- llm_request -> llm_response (mocked LLM)
- llm_response -> tool_proposed (_step_policy_check)
- tool_proposed -> tool_call (_step_tool_call)
- tool_call -> tool_result (part of _step_tool_call)
- tool_result -> llm_request (loop back; assert run_context)
- any -> terminated (assert final state)

Each test lives in tests/integration/. Not unit tests: need DB, real
 models (ADR-0001), real engine methods.

## Test shape

Seed a pending run with a tool granted. Write an llm_response step
with a tool_calls array. Call _step_policy_check (writes tool_proposed).
Then call _step_tool_call. Assert that execution_steps contains both
tool_proposed and tool_result, and that tool_rejected is absent.

## Coverage target

Not 100% theoretical. Target: every pair that has ever failed, plus
every pair where writer and reader were changed in separate commits.

## Alternatives considered

- Property-based testing across all steps. Higher coverage, higher
  setup cost. Good Stage 7 goal.
- Single e2e test that exercises whole loop. Already exists
  (smoke_e2e.py), catches Stage 2 bug. But fails all at once, not at the
  specific pair. Integration tests fail faster and name the pair.
- Formal spec of step protocol. Overkill for 12-step state machine.

## Consequences

Positive:
- Writer-reader mismatches fail with a test name that names the pair.
- Changing a step's output shape has a natural test to update.
- Tests document the contract.

Negative:
- Each test needs DB seeding + engine wiring. ~50 lines x 12 pairs =
  ~600 lines.
- Slower than unit tests. Acceptable.

Risks:
- Integration tests can be flaky with real external services.
  Mitigation: stub LLM, real DB@ (where contract lives).

## Revisit when

- Workflow engine (Stage 3) has its own steps - pattern applies directly.
- Test runtime exceeds 60s - split into separate CI job.
