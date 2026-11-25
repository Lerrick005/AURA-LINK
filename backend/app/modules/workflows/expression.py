"""Safe JSONLogic evaluator for workflow condition nodes.

No `eval()`, no `exec()`. Pure Python.
Supports a strict allowlist of operators. Recursion is bounded to prevent DoS.
"""
from __future__ import annotations

import operator
from typing import Any

MAX_DEPTH = 20


class ExpressionError(Exception):
    """Raised when an expression is malformed or uses an unsupported operator."""


def evaluate(expression: Any, context: dict[str, Any], _depth: int = 0) -> Any:
    """Evaluate a JSONLogic expression against a context dict."""
    if _depth > MAX_DEPTH:
        raise ExpressionError(f"expression depth exceeds {MAX_DEPTH}")

    # Primitives
    if isinstance(expression, (bool, int, float, str)) or expression is None:
        return expression

    if not isinstance(expression, dict) or len(expression) != 1:
        raise ExpressionError(f"invalid expression node: {expression!r}")

    op, args = next(iter(expression.items()))
    if not isinstance(args, list):
        args = [args]

    # Resolve variables
    if op == "var":
        var_path = args[0]
        default = args[1] if len(args) > 1 else None
        current = context
        for part in var_path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    # Evaluate child nodes
    evaluated_args = [evaluate(arg, context, _depth + 1) for arg in args]

    # Logic operators
    if op == "and":
        return all(evaluated_args)
    if op == "or":
        return any(evaluated_args)
    if op == "!":
        return not evaluated_args[0]

    # Comparison operators
    if len(evaluated_args) != 2:
        raise ExpressionError(f"operator {op!r} requires exactly 2 arguments")

    a, b = evaluated_args
    try:
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == "in":
            return a in b
    except TypeError as exc:
        raise ExpressionError(f"type error in operator {op!r}: {exc}") from exc

    raise ExpressionError(f"unsupported operator: {op!r}")


__all__ = ["evaluate", "ExpressionError"]
