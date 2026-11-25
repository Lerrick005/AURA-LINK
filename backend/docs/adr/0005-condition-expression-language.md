# ADR-0005: Condition Expression Language (JSONLogic)

Status: Accepted
Date: 2026-11-24

## Context
Workflow condition nodes need to evaluate boolean logic against the workflow state and input. 
The blueprint mandates that untrusted content is never treated as instructions, and deterministic code handles constraints. However, workflow authors (users) need a way to express logic like:
`if user.tier == "vip" and order.total > 500: route to premium_agent`

We need a language that is expressive enough for routing, but safe from prompt injection, remote code execution (RCE), and infinite loops (DoS).

## Decision
We will use **JSONLogic**. 
- Expressions are stored as JSON in the workflow graph (e.g., `{"and": [{">": [{"var": "order.total"}, 500]}, {"==": [{"var": "user.tier"}, "vip"]}]}`).
- The evaluation is pure Python (no `eval` or `exec`).
- The engine restricts the operator set to a safe allowlist (e.g., `==`, `!=`, `>`, `<`, `and`, `or`, `not`, `var`, `in`).
- Execution has a strict recursion limit to prevent stack overflows.

## Alternatives Considered
- **Python `eval()`**: Rejected. Catastrophic RCE risk. A workflow author could inject `__import__('os').system('rm -rf /')`.
- **Jinja2**: Rejected. While it has a sandbox, Jinja2 is designed for templating, not boolean logic, and sandbox escapes are a known class of bugs.
- **CEL (Common Expression Language)**: Excellent, but requires bringing in a heavy Go/Java compiler or a complex Python wrapper. Overkill for MVP.

## Consequences
- Workflow authors must learn JSONLogic syntax. The UI will eventually provide a visual builder that generates this JSON.
- The engine cannot execute arbitrary Python functions inside a condition.
- We must implement and maintain a small, safe evaluator in `app/modules/workflows/expression.py`.

## Revisit When
- Customers require string manipulation (e.g., regex matching) that JSONLogic doesn't support.
- The visual editor makes JSONLogic too cumbersome for complex nested conditions.
