"""Pure decision function tests. No DB, no LLM, no engine."""
from decimal import Decimal

import pytest

from app.modules.workflows.decision import decide_next_node
from app.modules.workflows.schemas import (
    Branch, Node, NodeConfig, NodeType, WorkflowGraph,
)
from app.modules.workflows.types import (
    NodeExec, WorkflowState,
    TERMINAL_BUDGET_EXCEEDED, TERMINAL_ESCALATED, TERMINAL_TIMED_OUT,
)


def _graph():
    return WorkflowGraph(
        entry_node_id="trig",
        nodes=[
            Node(id="trig", type=NodeType.TRIGGER, next="agent1"),
            Node(id="agent1", type=NodeType.AGENT, next="end",
                 config=NodeConfig(agent_id="ag")),
            Node(id="end", type=NodeType.TERMINATE,
                 config=NodeConfig(reason="completed")),
        ],
    )


def _state(last_node_id=None, execs=(), nodes=0, agents=0, tools=0,
           cost=Decimal("0"), elapsed=0.0):
    return WorkflowState(
        graph=_graph(),
        last_node_id=last_node_id,
        node_executions=tuple(execs),
        node_count=nodes,
        agent_call_count=agents,
        tool_call_count=tools,
        cost_usd=cost,
        elapsed_seconds=elapsed,
    )


def test_initial_dispatch_to_entry():
    d = decide_next_node(_state())
    assert d.kind == "run_node"
    assert d.node_id == "trig"


def test_trigger_dispatches_to_next():
    execs = [NodeExec("trig", "trigger", "ok", {"input": {}}, 0.0)]
    d = decide_next_node(_state(last_node_id="trig", execs=execs))
    assert d.kind == "run_node" and d.node_id == "agent1"


def test_agent_dispatches_to_next():
    execs = [
        NodeExec("trig", "trigger", "ok", {}, 0.0),
        NodeExec("agent1", "agent", "ok", {"output": "x"}, 1.0),
    ]
    d = decide_next_node(_state(last_node_id="agent1", execs=execs))
    assert d.kind == "run_node" and d.node_id == "end"


def test_terminate_node_terminates():
    execs = [
        NodeExec("trig", "trigger", "ok", {}, 0.0),
        NodeExec("agent1", "agent", "ok", {}, 1.0),
        NodeExec("end", "terminate", "ok", {"reason": "completed"}, 2.0),
    ]
    d = decide_next_node(_state(last_node_id="end", execs=execs))
    assert d.kind == "terminate"
    assert d.terminal_reason == "completed"


def test_time_limit_terminates():
    d = decide_next_node(_state(elapsed=500.0))
    assert d.kind == "terminate"
    assert d.terminal_reason == TERMINAL_TIMED_OUT


def test_node_limit_terminates():
    d = decide_next_node(_state(nodes=100))
    assert d.kind == "terminate"
    assert d.terminal_reason == TERMINAL_BUDGET_EXCEEDED
    assert d.terminal_detail == "max_nodes_executed"


def test_cost_limit_terminates():
    d = decide_next_node(_state(cost=Decimal("10.00")))
    assert d.kind == "terminate"
    assert d.terminal_reason == TERMINAL_BUDGET_EXCEEDED
    assert d.terminal_detail == "max_cost_usd"


def test_condition_without_chosen_next_escalates():
    g = WorkflowGraph(
        entry_node_id="trig",
        nodes=[
            Node(id="trig", type=NodeType.TRIGGER, next="cond"),
            Node(id="cond", type=NodeType.CONDITION, branches=[
                Branch(when=True, next="end_ok"),
                Branch(when=False, next="end_fail"),
            ]),
            Node(id="end_ok", type=NodeType.TERMINATE,
                 config=NodeConfig(reason="completed")),
            Node(id="end_fail", type=NodeType.TERMINATE,
                 config=NodeConfig(reason="failed")),
        ],
    )
    execs = [
        NodeExec("trig", "trigger", "ok", {}, 0.0),
        NodeExec("cond", "condition", "ok", {}, 1.0),  # no chosen_next_node_id
    ]
    state = WorkflowState(
        graph=g, last_node_id="cond", node_executions=tuple(execs),
        node_count=2, agent_call_count=0, tool_call_count=0,
        cost_usd=Decimal("0"), elapsed_seconds=1.0,
    )
    d = decide_next_node(state)
    assert d.kind == "terminate"
    assert d.terminal_reason == TERMINAL_ESCALATED


def test_condition_with_chosen_next_dispatches():
    g = WorkflowGraph(
        entry_node_id="trig",
        nodes=[
            Node(id="trig", type=NodeType.TRIGGER, next="cond"),
            Node(id="cond", type=NodeType.CONDITION, branches=[
                Branch(when=True, next="end_ok"),
                Branch(when=False, next="end_fail"),
            ]),
            Node(id="end_ok", type=NodeType.TERMINATE,
                 config=NodeConfig(reason="completed")),
            Node(id="end_fail", type=NodeType.TERMINATE,
                 config=NodeConfig(reason="failed")),
        ],
    )
    execs = [
        NodeExec("trig", "trigger", "ok", {}, 0.0),
        NodeExec("cond", "condition", "ok", {"chosen_next_node_id": "end_fail"}, 1.0),
    ]
    state = WorkflowState(
        graph=g, last_node_id="cond", node_executions=tuple(execs),
        node_count=2, agent_call_count=0, tool_call_count=0,
        cost_usd=Decimal("0"), elapsed_seconds=1.0,
    )
    d = decide_next_node(state)
    assert d.kind == "run_node"
    assert d.node_id == "end_fail"
