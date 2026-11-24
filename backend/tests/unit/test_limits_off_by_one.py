"""Regression: budget limits must terminate AT the limit, not one past it."""
import pytest
from app.modules.agents.schemas import AgentConfig, AgentLimits
from app.modules.runtime.decision import RunState, decide_next_step


def _cfg(max_steps=3, max_tool_calls=2, max_seconds=60.0, max_cost_usd=1.0):
    return AgentConfig(
        instructions="x", model="gpt-4o-mini",
        limits=AgentLimits(
            max_steps=max_steps, max_tool_calls=max_tool_calls,
            max_seconds=max_seconds, max_cost_usd=max_cost_usd,
        ),
        expected_terminal_tool=None, require_verified_terminal=False,
    )


def _state(config, **kw):
    base = dict(
        steps=(), iteration_count=0, tool_call_count=0, cost_usd=0.0,
        elapsed_seconds=0.0, consecutive_rejections=0, nudge_issued=False,
        config=config, terminal_tool_status="not_run",
        pending_proposal_count=0, last_step_type=None,
    )
    base.update(kw)
    return RunState(**base)


@pytest.mark.parametrize("iterations,expect_terminal", [
    (2, False), (3, True), (4, True),
])
def test_max_steps_terminates_at_limit(iterations, expect_terminal):
    d = decide_next_step(_state(_cfg(max_steps=3), iteration_count=iterations))
    if expect_terminal:
        assert d.kind == "terminate"
        assert d.terminal_reason == "budget_exceeded"
    else:
        assert d.kind != "terminate"


@pytest.mark.parametrize("calls,expect_terminal", [(1, False), (2, True), (3, True)])
def test_max_tool_calls_terminates_at_limit(calls, expect_terminal):
    d = decide_next_step(_state(_cfg(max_tool_calls=2), tool_call_count=calls))
    if expect_terminal:
        assert d.kind == "terminate" and d.terminal_reason == "budget_exceeded"
    else:
        assert d.kind != "terminate"


@pytest.mark.parametrize("cost,expect_terminal", [(0.99, False), (1.0, True), (1.01, True)])
def test_max_cost_terminates_at_limit(cost, expect_terminal):
    d = decide_next_step(_state(_cfg(max_cost_usd=1.0), cost_usd=cost))
    if expect_terminal:
        assert d.kind == "terminate" and d.terminal_reason == "budget_exceeded"
    else:
        assert d.kind != "terminate"


@pytest.mark.parametrize("secs,expect_terminal", [(59.9, False), (60.0, True), (60.1, True)])
def test_max_seconds_terminates_at_limit(secs, expect_terminal):
    d = decide_next_step(_state(_cfg(max_seconds=60.0), elapsed_seconds=secs))
    if expect_terminal:
        assert d.kind == "terminate" and d.terminal_reason == "timed_out"
    else:
        assert d.kind != "terminate"