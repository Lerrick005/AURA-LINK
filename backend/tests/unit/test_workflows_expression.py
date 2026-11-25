"""Unit tests for the safe JSONLogic evaluator."""
import pytest
from app.modules.workflows.expression import evaluate, ExpressionError

def test_primitive_literals():
    assert evaluate(True, {}) is True
    assert evaluate(42, {}) == 42
    assert evaluate("hello", {}) == "hello"

def test_var_lookup():
    ctx = {"user": {"tier": "vip", "age": 30}}
    assert evaluate({"var": "user.tier"}, ctx) == "vip"
    assert evaluate({"var": "user.age"}, ctx) == 30
    assert evaluate({"var": "user.missing"}, ctx) is None
    assert evaluate({"var": ["user.missing", "fallback"]}, ctx) == "fallback"

def test_comparisons():
    assert evaluate({"==": [1, 1]}, {}) is True
    assert evaluate({"!=": [1, 2]}, {}) is True
    assert evaluate({">": [10, 5]}, {}) is True
    assert evaluate({"<=": [5, 5]}, {}) is True

def test_logic_operators():
    assert evaluate({"and": [True, True]}, {}) is True
    assert evaluate({"or": [False, True]}, {}) is True
    assert evaluate({"!": [False]}, {}) is True

def test_complex_expression():
    ctx = {"order": {"total": 600}, "user": {"tier": "vip"}}
    expr = {
        "and": [
            {">": [{"var": "order.total"}, 500]},
            {"==": [{"var": "user.tier"}, "vip"]}
        ]
    }
    assert evaluate(expr, ctx) is True

def test_unsupported_operator():
    with pytest.raises(ExpressionError, match="unsupported operator"):
        evaluate({"unknown_op": [1, 2]}, {})

def test_depth_limit():
    expr = True
    for _ in range(25):
        expr = {"and": [expr, True]}
    with pytest.raises(ExpressionError, match="depth"):
        evaluate(expr, {})
