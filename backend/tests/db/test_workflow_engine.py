"""Integration tests for WorkflowEngine.

Proves the engine can traverse graphs, dispatch nodes, handle sub-runs,
and propagate terminal states. Uses the `world` fixture from conftest.py.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import text

from app.core.db import tenant_session
from app.core.ids import uuid7str
from app.modules.runtime.engine import RuntimeEngine
from app.modules.runtime.types import EngineConfig, EngineError, ToolResult
from app.modules.workflows.engine import WorkflowEngine
from app.modules.workflows.schemas import Branch, Node, NodeConfig, NodeType, WorkflowGraph
from app.modules.workflows.service import create_workflow, create_version as create_wf_version, deploy_version
from app.modules.workflows.types import TERMINAL_COMPLETED, TERMINAL_ESCALATED


@pytest.fixture
def engine():
    """Construct a WorkflowEngine with mocked LLM and tool registry."""
    config = EngineConfig(lease_ttl_seconds=30, heartbeat_interval_seconds=5)
    agent_engine = RuntimeEngine(
        session_factory=AsyncMock(),
        llm=Mock(),
        config=config,
        tool_registry=AsyncMock(),
    )
    return WorkflowEngine(
        session_factory=AsyncMock(),
        llm=Mock(),
        config=config,
        tool_registry=AsyncMock(),
        agent_engine=agent_engine,
    )


async def _setup_workflow_run(org_id: str, graph: WorkflowGraph):
    """Helper to create a workflow, deploy it, and insert a pending run."""
    run_id = uuid7str()
    wv_id = uuid7str()
    
    async with tenant_session(org_id) as s:
        # 1. Create and deploy workflow
        wf = await create_workflow(s, org_id=org_id, name="test-wf")
        ver = await create_wf_version(s, org_id=org_id, workflow_id=wf.id, graph=graph)
        await deploy_version(s, org_id=org_id, workflow_id=wf.id, workflow_version_id=ver.id)

        # 2. Insert a dummy agent to satisfy FK
        agent_id = uuid7str()
        av_id = uuid7str()
        await s.execute(text("INSERT INTO agents (id, org_id, name, status) VALUES (:id, :o, 'dummy', 'draft')"), {"id": agent_id, "o": org_id})
        await s.execute(text("INSERT INTO agent_versions (id, agent_id, org_id, version, config) VALUES (:av, :id, :o, 1, '{}')"), {"av": av_id, "id": agent_id, "o": org_id})
        await s.execute(text("UPDATE agents SET status='active', current_version_id=:av WHERE id=:id"), {"av": av_id, "id": agent_id})

        # 3. Insert the run row
        await s.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, org_id, agent_version_id, workflow_version_id, status, input) "
                "VALUES (:id, :o, :av, :wv, 'pending', CAST(:inp AS jsonb))"
            ),
            {"id": run_id, "o": org_id, "av": av_id, "wv": ver.id, "inp": json.dumps({"task": "test"})}
        )
    return run_id, ver.id


@pytest.mark.asyncio
async def test_workflow_minimal_success(world, engine):
    """Trigger -> Terminate. No agents involved."""
    org_id = world.a.org_id
    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="end"),
            Node(id="end", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ]
    )
    run_id, _ = await _setup_workflow_run(org_id, graph)

    terminal = await engine.run_once(run_id=run_id, org_id=org_id)
    assert terminal == TERMINAL_COMPLETED

    async with tenant_session(org_id) as s:
        run = (await s.execute(text("SELECT status FROM agent_runs WHERE id = :id"), {"id": run_id})).scalar_one()
        assert run == "completed"
        
        steps = (await s.execute(
            text("SELECT type, node_id FROM execution_steps WHERE execution_id = :id ORDER BY seq"), 
            {"id": run_id}
        )).all()
        assert len(steps) == 2
        assert steps[0].type == "trigger" and steps[0].node_id == "start"
        assert steps[1].type == "terminated"
        # NOTE: _write_terminated does not set node_id at the column level.
        # The terminal node id lives in detail JSON. Fix when RunPrimitives is
        # extracted in 3.1e.


@pytest.mark.asyncio
async def test_workflow_agent_success(world, engine):
    """Trigger -> Agent -> Terminate. Proves sub-run creation and aggregation."""
    org_id = world.a.org_id
    
    # Create a real agent to run
    agent_id = uuid7str()
    av_id = uuid7str()
    async with tenant_session(org_id) as s:
        await s.execute(text("INSERT INTO agents (id, org_id, name, status) VALUES (:id, :o, 'worker', 'draft')"), {"id": agent_id, "o": org_id})
        await s.execute(text("INSERT INTO agent_versions (id, agent_id, org_id, version, config) VALUES (:av, :id, :o, 1, CAST(:c AS jsonb))"), {"av": av_id, "id": agent_id, "o": org_id, "c": json.dumps({"instructions": "echo"})})
        await s.execute(text("UPDATE agents SET status='active', current_version_id=:av WHERE id=:id"), {"av": av_id, "id": agent_id})

    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="do_work"),
            Node(id="do_work", type=NodeType.AGENT, next="end", config=NodeConfig(agent_id=agent_id)),
            Node(id="end", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ]
    )
    run_id, wv_id = await _setup_workflow_run(org_id, graph)

    # Mock the sub-run to succeed immediately so we don't need real LLM loops
    with patch.object(engine._agent_engine, "run_once", return_value=TERMINAL_COMPLETED):
        terminal = await engine.run_once(run_id=run_id, org_id=org_id)
        assert terminal == TERMINAL_COMPLETED

    async with tenant_session(org_id) as s:
        run = (await s.execute(text("SELECT status FROM agent_runs WHERE id = :id"), {"id": run_id})).scalar_one()
        assert run == "completed"

        steps = (await s.execute(
            text("SELECT type, node_id, output FROM execution_steps WHERE execution_id = :id ORDER BY seq"), 
            {"id": run_id}
        )).all()
        assert len(steps) == 3
        assert steps[1].type == "agent" and steps[1].node_id == "do_work"
        
        sub_runs = (await s.execute(
            text("SELECT id, input FROM agent_runs WHERE workflow_version_id = :wv AND id != :parent"),
            {"wv": wv_id, "parent": run_id}
        )).all()
        assert len(sub_runs) == 1
        assert sub_runs[0].input["parent_run_id"] == run_id


@pytest.mark.asyncio
async def test_workflow_agent_failure_escalates(world, engine):
    """If a sub-run fails, the workflow escalates."""
    org_id = world.a.org_id
    
    agent_id = uuid7str()
    av_id = uuid7str()
    async with tenant_session(org_id) as s:
        await s.execute(text("INSERT INTO agents (id, org_id, name, status) VALUES (:id, :o, 'bad_worker', 'draft')"), {"id": agent_id, "o": org_id})
        await s.execute(text("INSERT INTO agent_versions (id, agent_id, org_id, version, config) VALUES (:av, :id, :o, 1, '{}')"), {"av": av_id, "id": agent_id, "o": org_id})
        await s.execute(text("UPDATE agents SET status='active', current_version_id=:av WHERE id=:id"), {"av": av_id, "id": agent_id})

    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="do_work"),
            Node(id="do_work", type=NodeType.AGENT, next="end", config=NodeConfig(agent_id=agent_id)),
            Node(id="end", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ]
    )
    run_id, _ = await _setup_workflow_run(org_id, graph)

    # Mock the sub-run to FAIL
    with patch.object(engine._agent_engine, "run_once", return_value=TERMINAL_ESCALATED):
        terminal = await engine.run_once(run_id=run_id, org_id=org_id)
        assert terminal == TERMINAL_ESCALATED

    async with tenant_session(org_id) as s:
        run = (await s.execute(text("SELECT status, terminal_reason FROM agent_runs WHERE id = :id"), {"id": run_id})).first()
        assert run.status == "escalated"
        assert run.terminal_reason == "escalated"


@pytest.mark.asyncio
async def test_workflow_condition_routes_true_branch(world, engine):
    """Condition evaluates to True -> routes to true branch."""
    org_id = world.a.org_id
    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="check"),
            Node(
                id="check", type=NodeType.CONDITION,
                branches=[
                    Branch(when={"==": [{"var": "task"}, "vip"]}, next="end_vip"),
                    Branch(when=True, next="end_std"),
                ],
            ),
            Node(id="end_vip", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
            Node(id="end_std", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ],
    )
    # _setup_workflow_run hardcodes {"task": "test"} — override to "vip"
    run_id, _ = await _setup_workflow_run(org_id, graph)
    async with tenant_session(org_id) as s:
        await s.execute(
            text("UPDATE agent_runs SET input = CAST(:inp AS jsonb) WHERE id = :i"),
            {"inp": json.dumps({"task": "vip"}), "i": run_id},
        )

    terminal = await engine.run_once(run_id=run_id, org_id=org_id)
    assert terminal == "completed"

    async with tenant_session(org_id) as s:
        steps = (await s.execute(
            text("SELECT type, node_id, output FROM execution_steps WHERE execution_id = :id ORDER BY seq"),
            {"id": run_id},
        )).all()
        # trigger, condition, terminate
        assert len(steps) == 3
        assert steps[1].type == "condition"
        assert steps[1].output["chosen_next_node_id"] == "end_vip"


@pytest.mark.asyncio
async def test_workflow_condition_routes_false_branch(world, engine):
    """Condition evaluates to False -> routes to catch-all."""
    org_id = world.a.org_id
    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="check"),
            Node(
                id="check", type=NodeType.CONDITION,
                branches=[
                    Branch(when={"==": [{"var": "task"}, "vip"]}, next="end_vip"),
                    Branch(when=True, next="end_std"),
                ],
            ),
            Node(id="end_vip", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
            Node(id="end_std", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ],
    )
    run_id, _ = await _setup_workflow_run(org_id, graph)
    # input is {"task": "test"} — doesn't match "vip", falls through to catch-all

    terminal = await engine.run_once(run_id=run_id, org_id=org_id)
    assert terminal == "completed"

    async with tenant_session(org_id) as s:
        steps = (await s.execute(
            text("SELECT type, node_id, output FROM execution_steps WHERE execution_id = :id ORDER BY seq"),
            {"id": run_id},
        )).all()
        assert len(steps) == 3
        assert steps[1].output["chosen_next_node_id"] == "end_std"


@pytest.mark.asyncio
async def test_workflow_condition_no_match_escalates(world, engine):
    """Condition with no matching branch and no catch-all -> escalate."""
    org_id = world.a.org_id
    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="check"),
            Node(
                id="check", type=NodeType.CONDITION,
                branches=[
                    Branch(when={"==": [{"var": "task"}, "vip"]}, next="end_vip"),
                    Branch(when={"==": [{"var": "task"}, "priority"]}, next="end_vip"),
                ],
            ),
            Node(id="end_vip", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ],
    )
    # input {"task": "test"} matches neither
    run_id, _ = await _setup_workflow_run(org_id, graph)

    terminal = await engine.run_once(run_id=run_id, org_id=org_id)
    assert terminal == "escalated"


class _MockExecutor:
    """A registered executor that echoes the arguments back."""
    def __init__(self, ok: bool = True):
        self._ok = ok

    async def __call__(self, *, tool, arguments, idempotency_key, timeout_seconds):
        if self._ok:
            return ToolResult(ok=True, output={"echoed": arguments}, verified=True)
        return ToolResult(ok=False, output={}, error=RuntimeError("boom"))


class _MockToolRegistry:
    """Minimal in-test registry: only 'echo' resolves."""
    def __init__(self, ok: bool = True):
        self._executor = _MockExecutor(ok=ok)

    def executor_for(self, tool_key: str):
        if tool_key == "echo":
            return self._executor
        raise EngineError(f"unknown tool: {tool_key}")

    def post_check_for(self, tool_key: str):
        return None


def _build_engine_with_registry(registry) -> WorkflowEngine:
    config = EngineConfig(lease_ttl_seconds=30, heartbeat_interval_seconds=5)
    agent_engine = RuntimeEngine(
        session_factory=AsyncMock(), llm=Mock(), config=config,
        tool_registry=AsyncMock(),
    )
    return WorkflowEngine(
        session_factory=AsyncMock(), llm=Mock(), config=config,
        tool_registry=registry, agent_engine=agent_engine,
    )


async def _seed_echo_tool(org_id: str) -> str:
    tool_id = uuid7str()
    async with tenant_session(org_id) as s:
        await s.execute(
            text(
                "INSERT INTO tools "
                "(id, org_id, key, name, description, input_schema, output_schema, "
                " risk_class, approval_policy, timeout_ms, idempotency_mode) "
                "VALUES (:id, :o, 'echo', 'Echo', 'Echo test tool', "
                "        CAST(:is AS jsonb), CAST(:os AS jsonb), "
                "        'read_only', 'auto', 5000, 'keyed')"
            ),
            {
                "id": tool_id, "o": org_id,
                "is": json.dumps({"type": "object"}),
                "os": json.dumps({"type": "object"}),
            },
        )
    return tool_id


@pytest.mark.asyncio
async def test_workflow_tool_node_success(world):
    """Trigger -> tool(echo) -> terminate. Proves tool dispatch + persistence."""
    org_id = world.a.org_id
    await _seed_echo_tool(org_id)

    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="do_tool"),
            Node(id="do_tool", type=NodeType.TOOL, next="end",
                 config=NodeConfig(tool_key="echo")),
            Node(id="end", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ],
    )
    run_id, _ = await _setup_workflow_run(org_id, graph)

    engine = _build_engine_with_registry(_MockToolRegistry(ok=True))
    terminal = await engine.run_once(run_id=run_id, org_id=org_id)
    assert terminal == TERMINAL_COMPLETED

    async with tenant_session(org_id) as s:
        steps = (await s.execute(
            text("SELECT type, node_id, status, output FROM execution_steps "
                 "WHERE execution_id = :id ORDER BY seq"),
            {"id": run_id},
        )).all()
        assert len(steps) == 3
        assert steps[1].type == "tool"
        assert steps[1].node_id == "do_tool"
        assert steps[1].status == "ok"
        assert steps[1].output["ok"] is True
        assert "echoed" in steps[1].output["result"]


@pytest.mark.asyncio
async def test_workflow_tool_node_failure_escalates(world):
    """Tool returns ok=False -> workflow escalates."""
    org_id = world.a.org_id
    await _seed_echo_tool(org_id)

    graph = WorkflowGraph(
        entry_node_id="start",
        nodes=[
            Node(id="start", type=NodeType.TRIGGER, next="do_tool"),
            Node(id="do_tool", type=NodeType.TOOL, next="end",
                 config=NodeConfig(tool_key="echo")),
            Node(id="end", type=NodeType.TERMINATE, config=NodeConfig(reason="completed")),
        ],
    )
    run_id, _ = await _setup_workflow_run(org_id, graph)

    engine = _build_engine_with_registry(_MockToolRegistry(ok=False))
    terminal = await engine.run_once(run_id=run_id, org_id=org_id)
    assert terminal == TERMINAL_ESCALATED

    async with tenant_session(org_id) as s:
        steps = (await s.execute(
            text("SELECT type, status FROM execution_steps "
                 "WHERE execution_id = :id ORDER BY seq"),
            {"id": run_id},
        )).all()
        # trigger, tool(failed), terminated(escalate)
        assert any(st.type == "tool" and st.status == "failed" for st in steps)
