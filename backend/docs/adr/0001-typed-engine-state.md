# ADR-0001: Typed engine state

Status: Accepted
Date: 2026-11-24
Deciders: Engineering

## Context

`execution_steps.input` and `execution_steps.output` are JSONB columns. Each
step type (llm_request, llm_response, tool_proposed, tool_executing,
tool_result, tool_rejected, verified, terminated, etc.) writes a different
shape. Later steps read those shapes back.

Today the shapes are implicit. A writer and a reader can silently disagree
about field names. This happened during Stage 2 exit-gate debugging:

- `_step_policy_check` wrote `tool_proposed.input` with `tool_id`,
  `arguments`, `call_id`, `idempotency_key`.
- The redactor stripped `tool_id` to `<redacted:str:len=36>` because the
  preserve-list didn't include it.
- `_step_tool_call` read `proposed_input["tool_id"]`, got the redacted
  placeholder, and passed it to a SQL lookup that returned no rows.
- The run terminated as `failed` with no diagnostic beyond "no tool_result
  step".

A second, independent bug in the same subsystem: the original redactor
stripped `llm_response.output.tool_calls[0].name` to a placeholder, so the
grant check saw `t.key = '<redacted:str:len=8>'` and rejected every
proposal with `not_granted`.

Both bugs are the same root cause: the shape of engine state is implicit
in the writer, and the reader has no compile-time or runtime contract to
check against.

## Decision

Define a Pydantic model per step type for the `input` and `output`
payloads. Both writers and readers import the same model. Serialization
to JSONB goes through `model.model_dump(mode="json")`. Deserialization
goes through `Model.model_validate(row["input"])`.

Models live in `app/modules/runtime/step_payloads.py`. One class per
step type: `LlmRequestInput`, `LlmResponseOutput`, `ToolProposedInput`,
`ToolExecutingInput`, `ToolResultOutput`, `ToolRejectedInput`,
`VerifiedInput`, `TerminatedInput`, `ResumedInput`, `LoadedConfigInput`.

A dispatch table maps `step_type` to `(input_model, output_model)`.

### Redaction becomes structural

`_redact_step_input` and `_redact_step_output` are removed. In their
place, a single `serialize_step_payload(step_type, payload, direction)`
function applies the per-model redaction policy declared in a single
table:

- `structural` - engine state, preserve verbatim
- `fail_closed` - redact everything
- `key_aware` - redact values whose key is sensitive

### Migration strategy

1. Add `step_payloads.py` with all models.
2. Change `_append_step` to accept a model instead of `dict`.
3. Change every writer to construct the model.
4. Change every reader to `Model.model_validate(row["input"])`.
5. Old rows remain readable via `ConfigDict(extra="ignore")`. Reader
   code catches `ValidationError` and treats it as "corrupt step,
   escalate" - never silently succeeds with missing data.

No DB migration needed.

## Alternatives considered

**A. TypedDict instead of Pydantic.** Cheaper, no runtime validation.
Rejected: the whole point is to catch shape mismatches at runtime.

**B. Database-level CHECK constraints on JSONB shape.** Rejected:
schema evolution becomes a migration per field, and JSONB CHECKs are
expensive per insert.

**C. Separate typed tables per step type.** Rejected: 12+ tables for
12+ step types, and the read-back pattern becomes a JOIN.

## Consequences

Positive:
- Writer-reader shape mismatches become `ValidationError` at
  deserialization, caught and escalated instead of failing silently.
- Redaction policy declared once per step type.
- mypy can be run against the models with `strict` mode.

Negative:
- ~300 lines of Pydantic models.
- One more place to change when adding a step type.
- Historical rows with redacted placeholders still parse but can't be
  replayed. Acceptable - those runs are already terminal.

## Revisit when

- A step type's payload needs to nest another model.
- The 12-model list grows past 20 (sign the state machine is too
  granular).
