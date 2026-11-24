# ADR-0005: Identifier discipline

---

Status: Accepted
Date: 2026-11-24
Deciders: Engineering

## Context

Two UUID generators in play:

- uuid7str() - UUIDv7, monotonically increasing (timestamp prefix),
  optimized for insert locality and index performance.
- uuid.uuid4() - UUIDv4, pure randomness.

UUIDv7's first 48 bits are a millisecond timestamp. Truncating a UUIDv7
to first N chars yields identical prefixes for UUIDs generated in the
same millisecond.

Across Stage 1 and Stage 2, this bit us four times:

1. org-{uuid7str()[:8]} in test_auth.py - organizations.name
   collision.
2. user-{uuid7str()[:8]} - users.email collision.
3. agent-{uuid7str()[:8]} in test_cleanup.py - agents.name
   collision.
4. e2e-{uuid7str()[:8]} in smoke_e2e.py - agents.name collision.

Each was a 15-30 minute debugging session. Pattern is universal:
uuid7str()[:N] is not unique within a test run.

## Decision

Three rules plus one meta-test.

### Rule 1 - uuid7str() for database primary keys only

DB PKs use uuid7str(). This is the only place timestamp prefix
matters (insert locality, B-tree index performance).

### Rule 2 - uuid.uuid4().hex[:12] for test-scoped identifiers

Any test-run-unique identifier uses uuid.uuid4().hex[:12] - 48 bits of
randomness, collision probability < 10-14 per million identifiers.

Includes: agent names, org names, user emails, tool keys, any string
with UNIQUE constraint generated in test setup.

### Rule 3 - never uuid7str()[:N]

No exceptions. The pattern is always wrong: works in production (low
generation rate), silently collides in tests (high rate, sub-ms
parallelism) and any bulk-insert path.

### Enforcement

tests/meta/test_id_discipline.py greps for the pattern uuid7str()[:
across app/ and tests/ and fails if found.

## Alternatives considered

- UUIDv4 everywhere. Loses insert-locality benefit of UUIDv7.
  B-tree inserts become random, page splits increase. At Auralink scale,
  perf difference is small but real. Not worth the loss.
- UUIDv7 everywhere but never truncate. Fixes bug but not
  test-uniqueness: test names need to be readable in logs, 12-char
  prefixes are the sweet spot.
- Monotonic counter for test names. Works but breaks parallel test
  execution.
- Random strings for tests. Same as UUID4 hxe prefixes but less
  convenient.

## Consequences

Positive:
- Entire class of prefix collision bugs eliminated.
- Tests can run in parallel (future CI).
- Rule enforceable with one grep.

Negative:
- Slightly less monotonic test names in logs. Timestamp in log lines
  provides chronology.
- One more meta-test to maintain.

Risks:
- Someone uses uuid4().hex[:6] (24 bits) instead of [:12]. Not
  caught. Mitigation: extend meta-test to check for truncation below 12
  hex chars.

## Revisit when

- If tests need sortable identifiers for chronological debugging, use
  timestamp-prefix + uuid4 suffix (see ADR content for example).
