"""Workflow graph validation: unique ids, DAG, node types, reachability."""
import pytest

from app.modules.workflows.graph import validate_graph, is_valid
from app.modules.workflows.schemas import (
    Branch, Node, NodeConfig, NodeType, WorkflowGraph,
)


def _trig(id="trig", next="end"):
    return Node(id=id, type=NodeType.TRIGGER, next=next)


def _term(id="end", reason="completed"):
    return Node(id=id, type=NodeType.TERMINATE, config=NodeConfig(reason=reason))


def _graph(nodes, entry="trig", edges=None):
    return WorkflowGraph(
        entry_node_id=entry,
        nodes=nodes,
        edges=edges or [],
    )


def test_minimal_valid_graph():
    g = _graph([_trig(), _term()])
    assert validate_graph(g) == []


def test_linear_valid_graph():
    nodes = [
        _trig(next="mid"),
        Node(id="mid", type=NodeType.TOOL, next="end",
             config=NodeConfig(tool_key="echo")),
        _term(),
    ]
    g = _graph(nodes)
    assert validate_graph(g) == []


def test_branching_valid_graph():
    nodes = [
        _trig(next="check"),
        Node(
            id="check", type=NodeType.CONDITION,
            branches=[
                Branch(when={"==": [1, 1]}, next="ok"),
                Branch(when=True, next="fail"),
            ],
        ),
        _term(id="ok", reason="completed"),
        _term(id="fail", reason="failed"),
    ]
    g = _graph(nodes)
    assert validate_graph(g) == []


def test_missing_trigger():
    g = _graph([_term()])
    errs = validate_graph(g)
    assert any("exactly one trigger" in e for e in errs)


def test_two_triggers():
    g = _graph([_trig(id="t1", next="t2"),
                Node(id="t2", type=NodeType.TRIGGER, next="end"),
                _term()])
    errs = validate_graph(g)
    assert any("exactly one trigger" in e for e in errs)


def test_entry_node_mismatch():
    g = _graph([_trig(id="trig"), _term()], entry="wrong")
    errs = validate_graph(g)
    assert any("entry_node_id" in e for e in errs)


def test_duplicate_node_id():
    g = _graph([_trig(), _term(), _term(id="trig")])
    errs = validate_graph(g)
    assert any("duplicate node id" in e for e in errs)


def test_dangling_edge():
    g = _graph([_trig(), _term()], edges=[("trig", "ghost")])
    errs = validate_graph(g)
    assert any("edge target not found" in e for e in errs)


def test_cycle_rejected():
    # trig -> a -> b -> a (cycle)
    nodes = [
        _trig(next="a"),
        Node(id="a", type=NodeType.TOOL, next="b", config=NodeConfig(tool_key="echo")),
        Node(id="b", type=NodeType.TOOL, next="a", config=NodeConfig(tool_key="echo")),
    ]
    g = _graph(nodes)
    errs = validate_graph(g)
    assert any("cycle" in e.lower() for e in errs)


def test_condition_needs_two_branches():
    nodes = [
        _trig(next="check"),
        Node(id="check", type=NodeType.CONDITION,
             branches=[Branch(when=True, next="end")]),
        _term(),
    ]
    g = _graph(nodes)
    errs = validate_graph(g)
    assert any("2+ branches" in e for e in errs)


def test_terminate_must_not_have_next():
    g = _graph([_trig(),
                Node(id="end", type=NodeType.TERMINATE, next="trig",
                     config=NodeConfig(reason="completed"))])
    errs = validate_graph(g)
    assert any("must not have 'next'" in e for e in errs)


def test_agent_node_requires_agent_id():
    nodes = [
        _trig(next="a"),
        Node(id="a", type=NodeType.AGENT, next="end"),  # no config
        _term(),
    ]
    g = _graph(nodes)
    errs = validate_graph(g)
    assert any("agent node 'a' requires config.agent_id" in e for e in errs)


def test_tool_node_requires_tool_key():
    nodes = [
        _trig(next="t"),
        Node(id="t", type=NodeType.TOOL, next="end", config=NodeConfig()),
        _term(),
    ]
    g = _graph(nodes)
    errs = validate_graph(g)
    assert any("tool node 't' requires config.tool_key" in e for e in errs)


def test_unreachable_node_rejected():
    nodes = [
        _trig(),  # trig -> end
        _term(),  # end (reachable)
        Node(id="orphan", type=NodeType.TOOL, next="end",
             config=NodeConfig(tool_key="echo")),  # unreachable from trig
    ]
    g = _graph(nodes)
    errs = validate_graph(g)
    assert any("unreachable" in e for e in errs)


def test_is_valid_convenience():
    assert is_valid(_graph([_trig(), _term()])) is True
    assert is_valid(_graph([_term()])) is False  # no trigger
